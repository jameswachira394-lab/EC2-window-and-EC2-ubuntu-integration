# MT5 Executor — Stable Startup Script

param(
    [string]$BindHost = "0.0.0.0",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

Write-Host "========================================="
Write-Host " Starting MT5 Execution Server"
Write-Host " Host: $BindHost"
Write-Host " Port: $Port"
Write-Host "========================================="

# Move to script directory
Set-Location $PSScriptRoot

# Load .env file if exists
$envFile = Join-Path $PSScriptRoot ".env"

if (Test-Path $envFile) {
    Write-Host "[INFO] Loading .env variables..."
    Get-Content $envFile | ForEach-Object {
        if ($_ -match "^\s*([^#][^=]+)=(.+)$") {
            $key = $Matches[1].Trim()
            $value = $Matches[2].Trim()
            [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
        }
    }
} else {
    Write-Host "[WARN] .env file not found"
}

# Validate required MT5 variables
foreach ($var in @("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER")) {
    if (-not [System.Environment]::GetEnvironmentVariable($var)) {
        Write-Host "[ERROR] Missing environment variable: $var"
        exit 1
    }
}

# Check Python exists
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Python is not installed or not in PATH"
    exit 1
}

Write-Host "[INFO] Starting Uvicorn server..."

# Start server
python -m uvicorn main:app `
    --host $BindHost `
    --port $Port `
    --log-level info