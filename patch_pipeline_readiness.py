from pathlib import Path
import re

path = Path(r".\scripts\pipeline\run_market_pipeline.ps1")
text = path.read_text(encoding="utf-8-sig")

text = text.replace("http://localhost:8000", "http://127.0.0.1:8000")
text = text.replace("http://localhost:8001", "http://127.0.0.1:8001")

pattern = re.compile(
    r'Write-Host "=== Wait for API readiness \(timeout=\$timeoutSec`s\) ===".*?Write-Host "OK: API ready at \$url"',
    re.DOTALL
)

replacement = r'''Write-Host "=== Wait for API readiness (timeout=$timeoutSec`s) ==="
$deadline = (Get-Date).AddSeconds($timeoutSec)
$ready = $false

while ((Get-Date) -lt $deadline) {
  try {
    Invoke-RestMethod -Uri $url -Method Get -TimeoutSec 5 | Out-Null
    $ready = $true
    break
  }
  catch {
    Start-Sleep -Seconds 2
  }
}

if (-not $ready) {
  Write-Host "ERROR: API not ready after $timeoutSec`s: $url" -ForegroundColor Red
  Write-Host "Last API logs (tail 200):" -ForegroundColor Yellow
  docker compose logs api --tail 200
  exit 1
}

Write-Host "OK: API ready at $url" -ForegroundColor Green'''

text, count = pattern.subn(replacement, text, count=1)

if count == 0:
    raise SystemExit("Could not find readiness block to replace in run_market_pipeline.ps1")

path.write_text(text, encoding="utf-8")
print("Patched run_market_pipeline.ps1")
