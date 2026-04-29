$ErrorActionPreference = "Stop"

if (-not (Test-Path ".\.env")) {
    Copy-Item ".\.env.example" ".\.env"
    Write-Host "Created .env from .env.example. Fill PAPER_TRADING_API_TOKEN before testing protected APIs."
}

python .\trading_service.py doctor
