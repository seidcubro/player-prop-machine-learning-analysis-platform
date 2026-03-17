$ErrorActionPreference = "Stop"

function Replace-Once([string]$path, [string]$pattern, [string]$replacement, [string]$why) {
  $content = Get-Content $path -Raw
  $new = [regex]::Replace($content, $pattern, $replacement, 1)
  if ($new -eq $content) { throw "Patch failed ($why) in $path" }
  Set-Content -Path $path -Value $new -Encoding utf8
}

Write-Host "== Patch train.py SELECT to include recs_mean/recs_trend =="
$train = "services/training/train.py"

# Replace the SELECT list that ends with pmf.trend, pmf.label_actual
Replace-Once $train `
  "(?s)SELECT\s+pmf\.mean\s*,\s*pmf\.stddev\s*,\s*pmf\.weighted_mean\s*,\s*pmf\.trend\s*,\s*pmf\.label_actual" `
  "SELECT pmf.mean, pmf.stddev, pmf.weighted_mean, pmf.trend, pmf.recs_mean, pmf.recs_trend, pmf.label_actual" `
  "train SELECT list"

Write-Host "== Make training robust if recs_* are NULL (fillna) =="
# Ensure we fill nulls for recs_* before astype(float)
# Anchor on: X = df[FEATURE_COLS].astype(float)
Replace-Once $train `
  "(?m)^\s*X\s*=\s*df\[FEATURE_COLS\]\.astype\(float\)\s*$" `
  "    # ensure optional features are present + numeric`r`n    for c in FEATURE_COLS:`r`n        if c not in df.columns:`r`n            df[c] = 0.0`r`n    df[['recs_mean','recs_trend']] = df[['recs_mean','recs_trend']].fillna(0.0)`r`n    X = df[FEATURE_COLS].astype(float)" `
  "train fillna + X build"

Write-Host "== Patch eval.py SELECT to include recs_mean/recs_trend (future-proof) =="
$eval = "services/training/eval.py"
Replace-Once $eval `
  "(?s)pmf\.mean,\s*pmf\.stddev,\s*pmf\.weighted_mean,\s*pmf\.trend," `
  "pmf.mean, pmf.stddev, pmf.weighted_mean, pmf.trend, pmf.recs_mean, pmf.recs_trend," `
  "eval SELECT list"

Write-Host "== Patch players.py feature-history SELECT to include recs_mean/recs_trend =="
$players = "services/api/app/routes/players.py"

# The history query currently selects: as_of_game_date, opponent, mean, stddev, weighted_mean, trend
Replace-Once $players `
  "(?s)SELECT\s+as_of_game_date\s*,\s*opponent\s*,\s*mean\s*,\s*stddev\s*,\s*weighted_mean\s*,\s*trend\s+FROM\s+player_market_features" `
  "SELECT as_of_game_date, opponent, mean, stddev, weighted_mean, trend, recs_mean, recs_trend FROM player_market_features" `
  "players feature history SELECT"

Write-Host "== Patch players.py latest-features SELECT to include recs_mean/recs_trend (if present) =="
# There is usually a "latest features" SELECT used by projection endpoints; patch if it matches
$playersContent = Get-Content $players -Raw
if ($playersContent -match "SELECT\s+as_of_game_date,\s*opponent,\s*mean,\s*stddev,\s*weighted_mean,\s*trend\s") {
  $new = [regex]::Replace($playersContent,
    "(?s)SELECT\s+as_of_game_date,\s*opponent,\s*mean,\s*stddev,\s*weighted_mean,\s*trend\s",
    "SELECT as_of_game_date, opponent, mean, stddev, weighted_mean, trend, recs_mean, recs_trend ",
    1
  )
  Set-Content -Path $players -Value $new -Encoding utf8
  Write-Host "OK: patched a latest-features SELECT in players.py"
} else {
  Write-Host "NOTE: did not find exact latest-features SELECT pattern; history SELECT already patched."
}

Write-Host "== Done. Quick grep =="
rg -n "recs_mean|recs_trend" services/training/train.py services/training/eval.py services/api/app/routes/players.py | Out-Host
