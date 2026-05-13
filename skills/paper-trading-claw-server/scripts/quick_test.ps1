$ErrorActionPreference = "Stop"

if (-not (Test-Path ".\.env")) {
    Copy-Item ".\.env.example" ".\.env"
    Write-Host "Created .env from .env.example. Fill DEEPSEEK_DATA_API_KEY, or keep router_env in a parent directory. Review PAPER_TRADING_USER_ID before user-specific tests."
}

python .\trading_service.py doctor
