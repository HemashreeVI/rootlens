param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("user-service", "payment-service")]
    [string]$Service,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^v\d+$')]
    [string]$Version
)

Set-Location $PSScriptRoot

$ports = @{ "user-service" = 8001; "payment-service" = 8003 }
$keys = @{
    "user-service"    = "USER_SERVICE_VERSION"
    "payment-service" = "PAYMENT_SERVICE_VERSION"
}

# 1. Which version is running now?
$previous = "none"
try {
    $previous = (Invoke-RestMethod "http://localhost:$($ports[$Service])/version").version
} catch { }

if ($previous -eq $Version) {
    Write-Host "$Service is already running $Version. Nothing to do."
    return
}

Write-Host "Deploying $Service $Version (currently $previous)..."

# 2. Update the release manifest (.env). Docker Compose reads it automatically.
$envFile = Join-Path $PSScriptRoot ".env"
$key = $keys[$Service]
$lines = @()
if (Test-Path $envFile) {
    $lines = @(Get-Content $envFile | Where-Object { $_ -notmatch "^$key=" })
}
$lines += "$key=$Version"
Set-Content -Path $envFile -Value $lines

# 3. Release: recreate only this service's container.
docker compose up -d --no-deps --force-recreate $Service
if ($LASTEXITCODE -ne 0) {
    Write-Host "Deploy FAILED."
    exit 1
}

# 4. Wait until the service reports the new version.
$ready = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    try {
        $running = (Invoke-RestMethod "http://localhost:$($ports[$Service])/version").version
        if ($running -eq $Version) { $ready = $true; break }
    } catch { }
}
if (-not $ready) {
    Write-Host "WARNING: $Service did not report $Version within 20 seconds."
}

# 5. Record the deployment.
$body = @{
    service          = $Service
    version          = $Version
    previous_version = $previous
    deployed_by      = "deploy.ps1"
} | ConvertTo-Json

Invoke-RestMethod -Method Post "http://localhost:8002/deployments" `
    -ContentType "application/json" -Body $body | Out-Null

Write-Host "Done. $Service $previous -> $Version (recorded)."