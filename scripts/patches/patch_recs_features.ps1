$ErrorActionPreference = "Stop"

function Replace-Once([string]$path, [string]$pattern, [string]$replacement, [string]$why) {
  $content = Get-Content $path -Raw
  $new = [regex]::Replace($content, $pattern, $replacement, 1)
  if ($new -eq $content) { throw "Patch failed ($why) in $path" }
  Set-Content -Path $path -Value $new -Encoding utf8
}

Write-Host "== Restoring jobs.py to HEAD =="
git restore --source=HEAD -- services/api/app/routes/jobs.py

Write-Host "== Ensuring DB columns exist =="
docker exec -i player-prop-platform-postgres-1 psql -U app -d app -c "
ALTER TABLE player_market_features
  ADD COLUMN IF NOT EXISTS recs_mean  double precision,
  ADD COLUMN IF NOT EXISTS recs_trend double precision;
" | Out-Host

Write-Host "== Patch jobs.py upsert SQL to include recs_mean/recs_trend =="
Replace-Once "services/api/app/routes/jobs.py" `
  "(?s)INSERT INTO player_market_features\s*\(\s*player_id,\s*market_id,\s*as_of_game_date,\s*opponent,\s*lookback,\s*mean,\s*stddev,\s*weighted_mean,\s*trend\s*\)" `
  "INSERT INTO player_market_features (player_id, market_id, as_of_game_date, opponent, lookback, mean, stddev, weighted_mean, trend, recs_mean, recs_trend)" `
  "INSERT column list"

Replace-Once "services/api/app/routes/jobs.py" `
  "(?s)VALUES\s*\(\s*:player_id,\s*:market_id,\s*:as_of_game_date,\s*:opponent,\s*:lookback,\s*:mean,\s*:stddev,\s*:weighted_mean,\s*:trend\s*\)" `
  "VALUES (:player_id, :market_id, :as_of_game_date, :opponent, :lookback, :mean, :stddev, :weighted_mean, :trend, :recs_mean, :recs_trend)" `
  "VALUES bind list"

Replace-Once "services/api/app/routes/jobs.py" `
  "(?m)^\s*trend\s*=\s*EXCLUDED\.trend\s*,?\s*$" `
  "          trend = EXCLUDED.trend,`r`n          recs_mean = EXCLUDED.recs_mean,`r`n          recs_trend = EXCLUDED.recs_trend" `
  "DO UPDATE assignments"

Write-Host "== Insert recs_mean/recs_trend computation after tr = _trend_slope(window) =="
Replace-Once "services/api/app/routes/jobs.py" `
  "(?m)^(?<indent>\s*)tr\s*=\s*_trend_slope\(window\)\s*$" `
  "`${indent}tr = _trend_slope(window)`r`n`r`n`${indent}# Extra usage proxy features for receiving yards (rec_yds only)`r`n`${indent}recs_mu = None`r`n`${indent}recs_tr = None`r`n`${indent}if market_code == `"rec_yds`":`r`n`${indent}    rec_window = []`r`n`${indent}    for g in games[i - lookback:i]:`r`n`${indent}        try:`r`n`${indent}            rv = g.get(`"receptions`", 0.0)`r`n`${indent}            rec_window.append(float(rv) if rv is not None else 0.0)`r`n`${indent}        except Exception:`r`n`${indent}            rec_window.append(0.0)`r`n`${indent}    if len(rec_window) == lookback:`r`n`${indent}        recs_mu = _mean(rec_window)`r`n`${indent}        recs_tr = _trend_slope(rec_window)" `
  "insert compute block"

Write-Host "== Inject recs_* into db.execute params dict after ""trend"": tr, =="
Replace-Once "services/api/app/routes/jobs.py" `
  "(?m)^(?<indent>\s*)`"trend`"\s*:\s*tr\s*,\s*$" `
  "`${indent}`"trend`": tr,`r`n`${indent}`"recs_mean`": recs_mu,`r`n`${indent}`"recs_trend`": recs_tr," `
  "inject params"

Write-Host "== Patch FEATURE_COLS in players.py and train.py to include recs_* (idempotent) =="
function Set-FeatureCols([string]$path) {
  $content = Get-Content $path -Raw
  $pattern = '(?m)^FEATURE_COLS\s*=\s*\[.*?\]\s*$'
  if ($content -match $pattern) {
    $new = [regex]::Replace($content, $pattern, 'FEATURE_COLS = ["mean", "stddev", "weighted_mean", "trend", "recs_mean", "recs_trend"]', 1)
    Set-Content -Path $path -Value $new -Encoding utf8
  } else {
    Write-Host "WARN: FEATURE_COLS not found in $path (skipping)."
  }
}
Set-FeatureCols "services/api/app/routes/players.py"
Set-FeatureCols "services/training/train.py"

Write-Host "== Verifying presence in jobs.py =="
rg -n "recs_mean|recs_trend|recs_mu|recs_tr|rec_window|receptions" services/api/app/routes/jobs.py | Out-Host

Write-Host "== Done. Next: rebuild + rerun pipeline. =="
