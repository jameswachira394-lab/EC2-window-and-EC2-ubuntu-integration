# MT5 Executor — Windows EC2 Startup Script
# ─────────────────────────────────────────
# Loads credentials from .env and starts the FastAPI server.
# Run from the mt5-executor\ directory.

param(
    [string]$Host = "0.0.0.0",
    [int]$Port    = 8000
)

# ── Load .env file ────────────────────────────────────────────────
$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    Write-Host "[INFO] Loading credentials from .env" -ForegroundColor Cyan
    Get-Content $envFile | ForEach-Object {
        if ($_ -match "^\s*([^#][^=]+)=(.+)$") {
            $key   = $Matches[1].Trim()
            $value = $Matches[2].Trim()
            [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
        }
    }
} else {
    Write-Host "[WARN] No .env file found. Make sure MT5_LOGIN, MT5_PASSWORD, MT5_SERVER are set." -ForegroundColor Yellow
}

# ── Verify MT5 credentials present ───────────────────────────────
foreach ($var in @("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER")) {
    if (-not [System.Environment]::GetEnvironmentVariable($var)) {
        Write-Host "[ERROR] Missing environment variable: $var" -ForegroundColor Red
        Write-Host "        Copy .env.example to .env and fill in your credentials." -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "═══════════════════════════════════════════" -ForegroundColor Green
Write-Host "   MT5 Execution Server" -ForegroundColor Green
Write-Host "   Account : $env:MT5_LOGIN" -ForegroundColor Green
Write-Host "   Server  : $env:MT5_SERVER" -ForegroundColor Green
Write-Host "   Listening on http://${Host}:${Port}" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════" -ForegroundColor Green
Write-Host ""

# ── Start uvicorn ────────────────────────────────────────────────
python -m uvicorn mt5_server:app --host $Host --port $Port --reload
