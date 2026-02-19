<#
.SYNOPSIS
  Runs the end-to-end pipeline for one market (default: rec_yds) and smoke-tests a projection.

.DESCRIPTION
  Steps:
    1) Ensure docker compose stack is up
    2) Wait for API readiness (OpenAPI reachable)
    3) Build features (API job)
    4) Attach labels (API job)
    5) Train model (training container)
    6) Smoke-test: pick a valid player (or use -PlayerId) and request projection + history

  Terminal-only workflow.
  Assumes API at http://localhost:8000 and routes under /api/v1.
#>

param(
  [string]$MarketCode = "rec_yds",
  [int]$Lookback = 5,
  [string]$ModelName = "ridge_v1",
  [string]$PlayerSearch = "metcalf",
  [int]$PlayersLimit = 15,
  [int]$ApiWaitSeconds = 90,
  [int]$PlayerId = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step([string]$msg) {
  Write-Host ""
  Write-Host ("=== " + $msg + " ===")
}

function Require-Command([string]$cmd) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
    throw ("Required command not found on PATH: " + $cmd)
  }
}

function Wait-Api([string]$url, [int]$timeoutSeconds) {
  $start = Get-Date
  while ($true) {
    try {
      Invoke-RestMethod $url | Out-Null
      Write-Host ("OK: API ready at " + $url)
      return
    } catch {
      $elapsed = (Get-Date) - $start
      if ($elapsed.TotalSeconds -ge $timeoutSeconds) {
        Write-Host ("ERROR: API not ready after " + $timeoutSeconds + "s: " + $url)
        Write-Host "Last API logs (tail 200):"
        try { docker compose logs --tail 200 api | Out-Host } catch { Write-Host "Could not fetch api logs." }
        throw
      }
      Start-Sleep -Seconds 2
    }
  }
}

Require-Command "docker"

$ApiBase = "http://localhost:8000/api/v1"
$OpenApiUrl = "http://localhost:8000/openapi.json"

Write-Step "Repo root"
Write-Host (Get-Location)

Write-Step "Bring stack up"
docker compose up -d --build | Out-Host
docker compose ps | Out-Host

Write-Step ("Wait for API readiness (timeout=" + $ApiWaitSeconds + "s)")
Wait-Api $OpenApiUrl $ApiWaitSeconds

Write-Step ("Build features: market=" + $MarketCode + " lookback=" + $Lookback)
$bfUrl = "$ApiBase/jobs/build_features?market_code=$MarketCode&lookback=$Lookback"
$bf = Invoke-RestMethod -Method Post $bfUrl
$bf | ConvertTo-Json -Depth 30 | Out-Host

Write-Step ("Attach labels: market=" + $MarketCode)
$alUrl = "$ApiBase/jobs/attach_labels?market_code=$MarketCode"
$al = Invoke-RestMethod -Method Post $alUrl
$al | ConvertTo-Json -Depth 30 | Out-Host

Write-Step ("Train model: market=" + $MarketCode + " lookback=" + $Lookback + " model=" + $ModelName)
docker compose run --rm `
  -e MARKET_CODE=$MarketCode `
  -e LOOKBACK=$Lookback `
  -e MODEL_NAME=$ModelName `
  -e ARTIFACT_DIR=/artifacts `
  training | Out-Host

function Try-Projection([int]$playerId) {
  $projUrl = "$ApiBase/players/$playerId/projection_ml?market_code=$MarketCode&lookback=$Lookback"
  try {
    $p = Invoke-RestMethod $projUrl
    return @{ ok = $true; player_id = $playerId; projection = $p; url = $projUrl }
  } catch {
    $msg = $_.Exception.Message
    return @{ ok = $false; player_id = $playerId; error = $msg; url = $projUrl }
  }
}

$chosenId = 0
$projectionResult = $null

if ($PlayerId -gt 0) {
  Write-Step ("Smoke test: using explicit PlayerId=" + $PlayerId)
  $projectionResult = Try-Projection $PlayerId
  if (-not $projectionResult.ok) {
    Write-Host ("Projection failed for PlayerId=" + $PlayerId)
    Write-Host ("URL: " + $projectionResult.url)
    throw $projectionResult.error
  }
  $chosenId = $PlayerId
} else {
  Write-Step ("Smoke test: search players: '" + $PlayerSearch + "' (auto-pick first with features)")
  $playersUrl = "$ApiBase/players?search=$PlayerSearch&limit=$PlayersLimit&offset=0&include_total=true"
  $resp = Invoke-RestMethod $playersUrl
  $resp.players | Select-Object id, first_name, last_name, position, team, external_id | Format-Table | Out-Host

  if (-not $resp.players -or $resp.players.Count -lt 1) {
    throw ("No players returned for search='" + $PlayerSearch + "'. Try a different PlayerSearch.")
  }

  # Prefer realistic receiver positions first for rec_yds
  $candidates = @()
  if ($MarketCode -eq "rec_yds") {
    $candidates = $resp.players | Where-Object { $_.position -in @("WR","TE","RB") }
    if (-not $candidates -or $candidates.Count -lt 1) { $candidates = $resp.players }
  } else {
    $candidates = $resp.players
  }

  foreach ($pl in $candidates) {
    $playerIdCandidate = [int]$pl.id
    $res = Try-Projection $playerIdCandidate
    if ($res.ok) {
      $chosenId = $playerIdCandidate
      $projectionResult = $res
      Write-Host ("Selected player_id=" + $chosenId + " (" + $pl.first_name + " " + $pl.last_name + ", " + $pl.position + ")")
      break
    } else {
      Write-Host ("Skip player_id=" + $playerIdCandidate + " (" + $pl.first_name + " " + $pl.last_name + ", " + $pl.position + "): " + $res.error)
    }
  }

  if ($chosenId -eq 0 -or -not $projectionResult) {
    throw ("No candidate player from search='" + $PlayerSearch + "' had features for market=" + $MarketCode + " lookback=" + $Lookback + ". Try a different search or pass -PlayerId explicitly.")
  }
}

Write-Step "Projection response"
$projectionResult.projection | ConvertTo-Json -Depth 50 | Out-Host

Write-Step "Projection history (last 5)"
$histUrl = "$ApiBase/players/$chosenId/ml_projections?market_code=$MarketCode&model_name=$ModelName&lookback=$Lookback&limit=5"
$hist = Invoke-RestMethod $histUrl
$hist | ConvertTo-Json -Depth 50 | Out-Host

Write-Step "DONE"
Write-Host ("Pipeline complete for market=" + $MarketCode + " lookback=" + $Lookback + " model=" + $ModelName + " player_id=" + $chosenId)


