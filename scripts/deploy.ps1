<#
.SYNOPSIS
    Deploys all FunctionGraph functions for CBR-to-OBS migration.

.DESCRIPTION
    Packages and deploys the three FunctionGraph functions:
    1. cbr-obs-orchestrator    (APIG trigger) - Starts migration
    2. cbr-obs-status-checker  (Timer trigger, every 5 min) - Polls and advances jobs
    3. cbr-obs-cleanup         (Timer trigger, every 10 min) - Cleans up temp resources

    Also sets environment variables from .env file.

.PREREQUISITES
    - Huawei Cloud CLI (hcloud) installed and configured
    - Python 3.9+ installed
    - .env file configured (copy from .env.example)
    - OBS buckets created (run create_buckets.ps1)
    - IAM agency created (run setup_iam.ps1)

.EXAMPLE
    .\deploy.ps1
    .\deploy.ps1 -Region sa-argentina-1
#>

param(
    [string]$Region = "sa-argentina-1",
    [string]$AgencyName = "cbr_to_obs_agency"
)

$ErrorActionPreference = "Stop"

Write-Host "=== Deploying FunctionGraph Functions for CBR-to-OBS Migration ===" -ForegroundColor Cyan
Write-Host "Region: $Region"
Write-Host "Agency: $AgencyName"
Write-Host ""

$projectRoot = Split-Path -Parent $PSScriptRoot
$functionsDir = Join-Path $projectRoot "src\functions"

if (-not (Test-Path $functionsDir)) {
    Write-Host "ERROR: Functions directory not found at $functionsDir" -ForegroundColor Red
    exit 1
}

$envFile = Join-Path $projectRoot ".env"
if (-not (Test-Path $envFile)) {
    Write-Host "ERROR: .env file not found. Copy .env.example to .env and configure it." -ForegroundColor Red
    exit 1
}

Write-Host "Loading environment variables from .env..." -ForegroundColor Yellow
$envVars = @{}
Get-Content $envFile | ForEach-Object {
    if ($_ -match "^\s*([^#][^=]+)=(.*)$") {
        $key = $matches[1].Trim()
        $value = $matches[2].Trim()
        $envVars[$key] = $value
    }
}
Write-Host "  Loaded $($envVars.Count) variables." -ForegroundColor Green

$functions = @(
    @{
        Name = "cbr-obs-orchestrator"
        Path = Join-Path $functionsDir "orchestrator"
        Handler = "src.functions.orchestrator.handler.handler"
        Timeout = 300
        Memory = 512
        Trigger = "APIG"
        TimerConfig = $null
    },
    @{
        Name = "cbr-obs-status-checker"
        Path = Join-Path $functionsDir "status_checker"
        Handler = "src.functions.status_checker.handler.handler"
        Timeout = 300
        Memory = 512
        Trigger = "TIMER"
        TimerConfig = "5m"
    },
    @{
        Name = "cbr-obs-cleanup"
        Path = Join-Path $functionsDir "cleanup"
        Handler = "src.functions.cleanup.handler.handler"
        Timeout = 300
        Memory = 256
        Trigger = "TIMER"
        TimerConfig = "10m"
    },
    @{
        Name = "cbr-obs-api"
        Path = Join-Path $functionsDir "api"
        Handler = "src.functions.api.handler.handler"
        Timeout = 300
        Memory = 512
        Trigger = "APIG"
        TimerConfig = $null
    }
)

foreach ($func in $functions) {
    Write-Host ""
    Write-Host "Deploying: $($func.Name)" -ForegroundColor Yellow

    $zipFile = Join-Path $func.Path "$($func.Name).zip"
    $sharedDir = Join-Path $projectRoot "src\shared"
    $srcDir = Join-Path $projectRoot "src"

    Write-Host "  Packaging..."
    $tempDir = Join-Path $env:TEMP "fgs_deploy_$($func.Name)"
    if (Test-Path $tempDir) { Remove-Item $tempDir -Recurse -Force }
    New-Item -ItemType Directory -Path $tempDir -Force | Out-Null

    $destSrc = Join-Path $tempDir "src"
    New-Item -ItemType Directory -Path $destSrc -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $destSrc "shared") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $destSrc "functions") -Force | Out-Null

    Copy-Item -Path (Join-Path $sharedDir "*") -Destination (Join-Path $destSrc "shared") -Recurse -Force
    $funcDirName = Split-Path $func.Path -Leaf
    $destFuncDir = Join-Path $destSrc "functions\$funcDirName"
    New-Item -ItemType Directory -Path $destFuncDir -Force | Out-Null
    Copy-Item -Path (Join-Path $func.Path "*.py") -Destination $destFuncDir -Recurse -Force

    $initFiles = @(
        Join-Path $destSrc "__init__.py"
        Join-Path $destSrc "functions\__init__.py"
        Join-Path $destFuncDir "__init__.py"
    )
    foreach ($initFile in $initFiles) {
        if (-not (Test-Path $initFile)) { New-Item -ItemType File -Path $initFile -Force | Out-Null }
    }

    $reqFile = Join-Path $func.Path "requirements.txt"
    if (Test-Path $reqFile) {
        Copy-Item $reqFile -Destination $tempDir -Force
        Write-Host "  Installing dependencies..."
        Push-Location $tempDir
        pip install -r requirements.txt -t . --quiet 2>$null
        Pop-Location
    }

    Compress-Archive -Path (Join-Path $tempDir "*") -DestinationPath $zipFile -Force
    Remove-Item $tempDir -Recurse -Force

    Write-Host "  Creating/updating function..."

    $envArgs = ""
    foreach ($key in $envVars.Keys) {
        $envArgs += " --env-var.$key=$($envVars[$key])"
    }

    hcloud FunctionGraph CreateFunction `
        --region=$Region `
        --function_name=$($func.Name) `
        --package_type=Zip `
        --code_file=$zipFile `
        --runtime=Python3.9 `
        --handler=$($func.Handler) `
        --timeout=$($func.Timeout) `
        --memory_size=$($func.Memory) `
        --agency_name=$AgencyName `
        $envArgs

    if ($?) {
        Write-Host "  Function deployed." -ForegroundColor Green
    } else {
        Write-Host "  Trying to update existing function..." -ForegroundColor Yellow
        hcloud FunctionGraph UpdateFunction `
            --region=$Region `
            --function_name=$($func.Name) `
            --code_file=$zipFile `
            --handler=$($func.Handler) `
            --timeout=$($func.Timeout) `
            --memory_size=$($func.Memory) `
            $envArgs
    }

    if ($func.Trigger -eq "TIMER" -and $func.TimerConfig) {
        Write-Host "  Creating timer trigger ($($func.TimerConfig))..."
        hcloud FunctionGraph CreateTimerTrigger `
            --region=$Region `
            --function_name=$($func.Name) `
            --trigger_name="$($func.Name)-timer" `
            --schedule=$($func.TimerConfig)
    }

    if ($func.Trigger -eq "APIG") {
        Write-Host "  NOTE: Create APIG trigger manually in the console for $($func.Name)" -ForegroundColor Yellow
        Write-Host "        or use: hcloud APIG CreateApi ..." -ForegroundColor Yellow
    }

    Remove-Item $zipFile -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "=== Deployment Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Functions deployed:"
Write-Host "  cbr-obs-orchestrator    -> APIG trigger (invoke via HTTP)"
Write-Host "  cbr-obs-status-checker  -> Timer trigger (every 5 min)"
Write-Host "  cbr-obs-cleanup         -> Timer trigger (every 10 min)"
Write-Host "  cbr-obs-api             -> APIG trigger (REST API for frontend)"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Create APIG trigger for cbr-obs-orchestrator and cbr-obs-api in the console"
Write-Host "  2. Configure CORS on APIG to allow the frontend origin"
Write-Host "  3. Set VITE_API_BASE in frontend/.env to the cbr-obs-api APIG URL"
Write-Host "  4. Deploy frontend: .\scripts\deploy_frontend.ps1"
