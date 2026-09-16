$ErrorActionPreference = "Stop"
Write-Host "`n=== CBR Vaults ===`n" -ForegroundColor Cyan

$existing = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
foreach ($conn in $existing) { Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 500 }

python app.py
