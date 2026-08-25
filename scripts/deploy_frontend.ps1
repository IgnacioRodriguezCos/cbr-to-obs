<#
.SYNOPSIS
    Builds and deploys the React frontend to OBS static website hosting.

.DESCRIPTION
    1. Installs npm dependencies
    2. Builds the React app with Vite (outputs to frontend/dist/)
    3. Creates OBS bucket for frontend (if not exists)
    4. Uploads all static files to the bucket
    5. Configures static website hosting
    6. Prints the website URL

.PREREQUISITES
    - Node.js 18+ and npm installed
    - Huawei Cloud CLI (hcloud) installed and configured
    - API Gateway endpoint configured in frontend/.env

.EXAMPLE
    .\deploy_frontend.ps1
    .\deploy_frontend.ps1 -Region la-south-2
#>

param(
    [string]$Region = "sa-argentina-1",
    [string]$BucketName = "cbr-obs-frontend"
)

$ErrorActionPreference = "Stop"

Write-Host "=== Deploying Frontend to OBS ===" -ForegroundColor Cyan

$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendDir = Join-Path $projectRoot "frontend"

if (-not (Test-Path $frontendDir)) {
    Write-Host "ERROR: frontend directory not found at $frontendDir" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Step 1: Installing dependencies..." -ForegroundColor Yellow
Push-Location $frontendDir
npm install
if (-not $?) {
    Write-Host "ERROR: npm install failed" -ForegroundColor Red
    Pop-Location
    exit 1
}
Write-Host "  Dependencies installed." -ForegroundColor Green

Write-Host ""
Write-Host "Step 2: Building React app..." -ForegroundColor Yellow
npm run build
if (-not $?) {
    Write-Host "ERROR: npm run build failed" -ForegroundColor Red
    Pop-Location
    exit 1
}
Pop-Location
$distDir = Join-Path $frontendDir "dist"
Write-Host "  Build complete. Output: $distDir" -ForegroundColor Green

Write-Host ""
Write-Host "Step 3: Creating OBS bucket '$BucketName' in $Region..." -ForegroundColor Yellow
hcloud obs CreateBucket --bucket=$BucketName --region=$Region 2>$null
if ($?) {
    Write-Host "  Bucket created." -ForegroundColor Green
} else {
    Write-Host "  Bucket may already exist, continuing..." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Step 4: Setting bucket public read policy..." -ForegroundColor Yellow
$policy = @{
    Statement = @(
        @{
            Sid = "PublicReadGetObject"
            Effect = "Allow"
            Principal = "*"
            Action = "obs:GetObject"
            Resource = "arn:aws:s3:::$BucketName/*"
        }
    )
    Version = "2012-10-17"
} | ConvertTo-Json -Depth 5

$policyFile = New-TemporaryFile
Set-Content -Path $policyFile -Value $policy -Encoding UTF8
hcloud obs SetBucketPolicy --bucket=$BucketName --policy=@$policyFile 2>$null
Remove-Item $policyFile -Force
Write-Host "  Policy set." -ForegroundColor Green

Write-Host ""
Write-Host "Step 5: Configuring static website hosting..." -ForegroundColor Yellow
hcloud obs SetBucketWebsite `
    --bucket=$BucketName `
    --index_document=index.html `
    --error_document=index.html 2>$null
Write-Host "  Static hosting configured." -ForegroundColor Green

Write-Host ""
Write-Host "Step 6: Uploading files to OBS..." -ForegroundColor Yellow
$files = Get-ChildItem -Path $distDir -Recurse -File
$uploaded = 0
$failed = 0

foreach ($file in $files) {
    $relativePath = $file.FullName.Substring($distDir.Length + 1).Replace("\", "/")

    hcloud obs PutObject `
        --bucket=$BucketName `
        --key=$relativePath `
        --file=$($file.FullName) 2>$null

    if ($?) {
        $uploaded++
    } else {
        $failed++
        Write-Host "  FAILED: $relativePath" -ForegroundColor Red
    }
}

Write-Host "  Uploaded: $uploaded files, Failed: $failed files" -ForegroundColor $(if ($failed -eq 0) { "Green" } else { "Yellow" })

Write-Host ""
Write-Host "=== Frontend Deployment Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Website URL:"
Write-Host "  https://$BucketName.obs.$Region.myhuaweicloud.com" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Configure CORS on APIG to allow this origin"
Write-Host "  2. Set VITE_API_BASE in frontend/.env to your APIG URL"
Write-Host "  3. Rebuild and redeploy if VITE_API_BASE was changed"
