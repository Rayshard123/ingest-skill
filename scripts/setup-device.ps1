<#
.SYNOPSIS
  One-time local setup for Device A or B. Creates a non-synced Python runtime and host-bound profile.
#>
param(
    [Parameter(Mandatory)][ValidateSet("A", "B")][string]$Device,
    [Parameter(Mandatory)][string]$VaultRoot,
    [Parameter(Mandatory)][string]$WorkRoot,
    [string]$BasePython = "",
    [string]$RuntimeRoot = (Join-Path $env:LOCALAPPDATA "LeonWiki\ingest-runtime"),
    [string]$ProfilePath = (Join-Path $env:LOCALAPPDATA "LeonWiki\ingest-device.json")
)

$ErrorActionPreference = "Stop"

function Get-FullPath([string]$Value) {
    return [System.IO.Path]::GetFullPath($Value).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
}

function Test-PathWithin([string]$Parent, [string]$Child) {
    $parentPath = Get-FullPath $Parent
    $childPath = Get-FullPath $Child
    if ($parentPath -ieq $childPath) { return $true }
    return $childPath.StartsWith($parentPath + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
}

foreach ($value in @($VaultRoot, $WorkRoot, $RuntimeRoot, $ProfilePath)) {
    if (-not [System.IO.Path]::IsPathRooted($value)) {
        throw "All setup paths must be absolute: $value"
    }
}

$vault = Get-FullPath $VaultRoot
$work = Get-FullPath $WorkRoot
$runtime = Get-FullPath $RuntimeRoot
$profileFile = Get-FullPath $ProfilePath

if (-not (Test-Path -LiteralPath $vault -PathType Container)) {
    throw "Vault root does not exist: $vault"
}
foreach ($required in @("_SYSTEM.md", "_registry.md", "wiki", "_ops")) {
    if (-not (Test-Path -LiteralPath (Join-Path $vault $required))) {
        throw "Vault prerequisite is missing: $required"
    }
}
$rootMocs = @(Get-ChildItem -LiteralPath $vault -File -Filter "_MOC_*.md")
if ($rootMocs.Count -lt 1) {
    throw "Vault root MOC is missing."
}
foreach ($candidate in @($work, $runtime)) {
    if ((Test-PathWithin $vault $candidate) -or (Test-PathWithin $candidate $vault)) {
        throw "Local runtime/work path must not overlap the Vault: $candidate"
    }
}
if (Test-PathWithin $vault $profileFile) {
    throw "Device profile must be outside the Vault: $profileFile"
}

$governanceRoot = Join-Path (Split-Path -Parent $PSScriptRoot) "runtime\governance"
foreach ($required in @("governance_validate.py", "rebuild_registry.py", "frontmatter.py")) {
    if (-not (Test-Path -LiteralPath (Join-Path $governanceRoot $required) -PathType Leaf)) {
        throw "Bundled governance runtime is incomplete: $required"
    }
}

if ([string]::IsNullOrWhiteSpace($BasePython)) {
    $pythonCommand = Get-Command python -ErrorAction Stop
    $BasePython = $pythonCommand.Source
}
$basePythonPath = (Resolve-Path -LiteralPath $BasePython).Path
$versionText = & $basePythonPath -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
if ($LASTEXITCODE -ne 0) {
    throw "Base Python could not be executed: $basePythonPath"
}
$version = [Version]$versionText.Trim()
if ($version -lt [Version]"3.11") {
    throw "Python 3.11 or newer is required; found $version."
}

New-Item -ItemType Directory -Path $work -Force | Out-Null
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$profileParent = Split-Path -Parent $profileFile
New-Item -ItemType Directory -Path $profileParent -Force | Out-Null

$venv = Join-Path $runtime ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    & $basePythonPath -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create Python venv: $venv" }
}

$requirements = Join-Path (Split-Path -Parent $PSScriptRoot) "runtime\requirements.lock"
& $venvPython -m pip install --disable-pip-version-check --no-cache-dir --requirement $requirements
if ($LASTEXITCODE -ne 0) { throw "Failed to install pinned Ingest dependencies." }
& $venvPython -c "import jsonschema, yaml; print('Ingest Python dependencies OK')"
if ($LASTEXITCODE -ne 0) { throw "Pinned Ingest dependencies failed import validation." }

$profile = [ordered]@{
    schema_version = "1.0"
    device = $Device.ToUpperInvariant()
    hostname = [Environment]::MachineName
    vault_root = $vault
    work_root = $work
    runtime_root = $runtime
    python_executable = (Resolve-Path -LiteralPath $venvPython).Path
}
$json = $profile | ConvertTo-Json -Depth 3
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($profileFile, $json + [Environment]::NewLine, $utf8NoBom)

Write-Host "Ingest Device $($profile.device) setup complete." -ForegroundColor Green
Write-Host "Profile: $profileFile"
Write-Host "Work root: $work"
Write-Host "Python: $($profile.python_executable)"
