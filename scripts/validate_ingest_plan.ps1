<#
.SYNOPSIS
  Validate one untrusted Ingest plan using the host-bound device profile.
#>
param(
    [Parameter(Mandatory)][string]$Plan,
    [string]$ProfilePath = (Join-Path $env:LOCALAPPDATA "LeonWiki\ingest-device.json")
)

$ErrorActionPreference = "Stop"
$executor = Join-Path $PSScriptRoot "ingest_executor.py"
if (-not (Test-Path -LiteralPath $ProfilePath -PathType Leaf)) {
    Write-Error "Device profile is missing. Run setup-device.ps1 first: $ProfilePath"
    exit 1
}

try {
    $profile = Get-Content -LiteralPath $ProfilePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $python = (Resolve-Path -LiteralPath ([string]$profile.python_executable)).Path
    $resolvedProfile = (Resolve-Path -LiteralPath $ProfilePath).Path
    $resolvedPlan = (Resolve-Path -LiteralPath $Plan).Path
} catch {
    Write-Error "Invalid Ingest device profile or plan: $($_.Exception.Message)"
    exit 1
}

& $python -B $executor validate-plan --profile $resolvedProfile --plan $resolvedPlan
exit $LASTEXITCODE
