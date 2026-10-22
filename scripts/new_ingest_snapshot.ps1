<#
.SYNOPSIS
  Write a UTF-8 Ingest snapshot under the host profile's non-synced work root.
#>
param(
    [Parameter(Mandatory)][string[]]$Material,
    [Parameter(Mandatory)][string]$Out,
    [string]$ProfilePath = (Join-Path $env:LOCALAPPDATA "LeonWiki\ingest-device.json"),
    [switch]$Anchors
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
} catch {
    Write-Error "Invalid Ingest device profile: $($_.Exception.Message)"
    exit 1
}

$executorArgs = @("-B", $executor, "snapshot", "--profile", $resolvedProfile, "--out", $Out)
foreach ($item in $Material) {
    $executorArgs += @("--material", (Resolve-Path -LiteralPath $item).Path)
}
if ($Anchors) {
    $executorArgs += "--anchors"
}

& $python @executorArgs
exit $LASTEXITCODE
