<#
.SYNOPSIS
    Sets up IAM agency and permissions for FunctionGraph CBR-to-OBS migration.

.DESCRIPTION
    Creates an IAM agency that grants FunctionGraph the permissions needed
    to access CBR, EVS, IMS, and OBS services.
    The agency allows FunctionGraph to assume the role when executing functions.

.PREREQUISITES
    - Huawei Cloud CLI (hcloud) installed and configured
    - Admin-level IAM permissions

.EXAMPLE
    .\setup_iam.ps1
#>

$ErrorActionPreference = "Stop"

Write-Host "=== Setting Up IAM for CBR-to-OBS Migration ===" -ForegroundColor Cyan

$agencyName = "cbr_to_obs_agency"
$roleName   = "cbr_to_obs_role"

Write-Host ""
Write-Host "Step 1: Creating custom role '$roleName'..." -ForegroundColor Yellow

$rolePolicy = @'
{
    "Version": "1.1",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "cbr:*:*",
                "evs:*:*",
                "ims:*:*",
                "obs:*:*"
            ],
            "Resource": ["*"]
        }
    ]
}
'@

$tempRoleFile = New-TemporaryFile
Set-Content -Path $tempRoleFile -Value $rolePolicy -Encoding UTF8

hcloud IAM CreateCustomRole --role_name=$roleName --display_name="CBR to OBS Migration Role" --policy=@$tempRoleFile

if ($?) {
    Write-Host "  Role created successfully." -ForegroundColor Green
} else {
    Write-Host "  Role may already exist, continuing..." -ForegroundColor Yellow
}

Remove-Item $tempRoleFile -Force

Write-Host ""
Write-Host "Step 2: Creating IAM agency '$agencyName'..." -ForegroundColor Yellow

hcloud IAM CreateAgency `
    --agency_name=$agencyName `
    --agency_type="JOB" `
    --delegated_domain_name="fgs.myhuaweicloud.com" `
    --duration=3600

if ($?) {
    Write-Host "  Agency created successfully." -ForegroundColor Green
} else {
    Write-Host "  Agency may already exist, continuing..." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Step 3: Assigning role to agency..." -ForegroundColor Yellow

hcloud IAM AssignRoleToAgency --agency_name=$agencyName --role_name=$roleName

if ($?) {
    Write-Host "  Role assigned successfully." -ForegroundColor Green
} else {
    Write-Host "  Role assignment may have failed, check manually." -ForegroundColor Red
}

Write-Host ""
Write-Host "=== IAM Setup Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Agency: $agencyName"
Write-Host "Role:   $roleName"
Write-Host ""
Write-Host "Use this agency name when deploying FunctionGraph functions."
