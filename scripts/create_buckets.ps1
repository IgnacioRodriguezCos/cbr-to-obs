<#
.SYNOPSIS
    Creates OBS buckets for CBR-to-OBS migration in both regions.

.DESCRIPTION
    Creates the following buckets:
    - cbr-evs-buenosaires  (sa-argentina-1) - EVS disk backups from Buenos Aires
    - cbr-evs-santiago     (la-south-2) - EVS disk backups from Santiago
    - cbr-migration-state  (sa-argentina-1) - Migration job state files
    - cbr-obs-frontend     (sa-argentina-1) - Frontend static website hosting

.PREREQUISITES
    - Huawei Cloud CLI (hcloud) installed and configured
    - Run: hcloud configure set --cli-region=sa-argentina-1 --ak=YOUR_AK --sk=YOUR_SK

.EXAMPLE
    .\create_buckets.ps1
#>

$ErrorActionPreference = "Stop"

Write-Host "=== Creating OBS Buckets for CBR-to-OBS Migration ===" -ForegroundColor Cyan

$buckets = @(
    @{ Name = "cbr-evs-buenosaires";  Region = "sa-argentina-1"; Description = "EVS disk backups - Buenos Aires" }
    @{ Name = "cbr-evs-santiago";     Region = "la-south-2"; Description = "EVS disk backups - Santiago" }
    @{ Name = "cbr-migration-state";  Region = "sa-argentina-1"; Description = "Migration job state files" }
    @{ Name = "cbr-obs-frontend";     Region = "sa-argentina-1"; Description = "Frontend static website" }
)

foreach ($bucket in $buckets) {
    Write-Host ""
    Write-Host "Creating bucket: $($bucket.Name)" -ForegroundColor Yellow
    Write-Host "  Region: $($bucket.Region)"
    Write-Host "  Purpose: $($bucket.Description)"

    hcloud obs CreateBucket --bucket=$($bucket.Name) --region=$($bucket.Region)

    if ($?) {
        Write-Host "  Status: CREATED" -ForegroundColor Green
    } else {
        Write-Host "  Status: FAILED (may already exist)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "=== Bucket Creation Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Buckets created:"
Write-Host "  cbr-evs-buenosaires  -> Buenos Aires (sa-argentina-1)"
Write-Host "  cbr-evs-santiago     -> Santiago (la-south-2)"
Write-Host "  cbr-migration-state  -> Buenos Aires (sa-argentina-1)"
Write-Host "  cbr-obs-frontend     -> Buenos Aires (sa-argentina-1)"
