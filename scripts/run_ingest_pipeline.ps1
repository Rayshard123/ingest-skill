<#
.SYNOPSIS
  Host-bound Ingest entry point: untrusted plan -> deterministic executor -> staging governance -> commit.
#>
param(
    [Parameter(Mandatory)][string]$Plan,
    [string]$ProfilePath = (Join-Path $env:LOCALAPPDATA "LeonWiki\ingest-device.json"),
    [Parameter(Mandatory)][switch]$SyncConfirmed,
    [switch]$Resume,
    [switch]$SkipIndex
)

$ErrorActionPreference = "Stop"
$executor = Join-Path $PSScriptRoot "ingest_executor.py"

if (-not (Test-Path -LiteralPath $executor -PathType Leaf)) {
    Write-Error "Ingest executor is missing: $executor"
    exit 1
}
if (-not (Test-Path -LiteralPath $ProfilePath -PathType Leaf)) {
    Write-Error "Device profile is missing. Run setup-device.ps1 first: $ProfilePath"
    exit 1
}

try {
    $profile = Get-Content -LiteralPath $ProfilePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]::IsNullOrWhiteSpace([string]$profile.python_executable)) {
        throw "Profile has no python_executable."
    }
    if ([string]$profile.hostname -ine [Environment]::MachineName) {
        throw "Profile host '$($profile.hostname)' does not match '$([Environment]::MachineName)'."
    }
    $python = (Resolve-Path -LiteralPath ([string]$profile.python_executable)).Path
    $resolvedProfile = (Resolve-Path -LiteralPath $ProfilePath).Path
    $resolvedPlan = (Resolve-Path -LiteralPath $Plan).Path
} catch {
    Write-Error "Invalid Ingest device profile or plan: $($_.Exception.Message)"
    exit 1
}

$executorArgs = @(
    "-B",
    $executor,
    "execute",
    "--profile", $resolvedProfile,
    "--plan", $resolvedPlan,
    "--sync-confirmed"
)
if ($Resume) {
    $executorArgs += "--resume"
}
if ($SkipIndex) {
    $executorArgs += "--skip-index"
}

& $python @executorArgs
$nativeExitCode = $LASTEXITCODE
if ($nativeExitCode -ne 0) {
    Write-Error "Ingest failed with exit code $nativeExitCode. No success marker will be emitted."
    exit $nativeExitCode
}

Write-Host "Ingest transaction completed." -ForegroundColor Green
exit 0
