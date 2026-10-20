[CmdletBinding()]
param([switch]$SkipDependencyInstall)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($env:OS -ne "Windows_NT") {
    throw "The Windows executable must be built on Windows; PyInstaller does not cross-compile."
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ApiRoot = Join-Path $RepoRoot "apps\api"
$Artifact = Join-Path $ApiRoot "dist\edgeos-api.exe"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install it from https://docs.astral.sh/uv/ and retry."
}

Push-Location $ApiRoot
try {
    if (-not $SkipDependencyInstall) {
        uv sync --all-groups --locked --no-editable
        if ($LASTEXITCODE -ne 0) {
            throw "uv sync failed with exit code $LASTEXITCODE"
        }
    }

    uv run --locked --no-editable pyinstaller --clean --noconfirm edgeos-api.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $Artifact -PathType Leaf)) {
    throw "Expected artifact was not created: $Artifact"
}

$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Artifact).Hash.ToLowerInvariant()
Write-Output "EDGEOS API executable: $Artifact"
Write-Output "SHA256: $Hash"
