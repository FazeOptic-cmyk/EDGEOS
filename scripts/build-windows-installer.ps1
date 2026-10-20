[CmdletBinding()]
param(
    [switch]$SkipDependencyInstall,
    [switch]$SkipTests,
    [switch]$PayloadOnly,
    [switch]$PackageOnly,
    [switch]$SkipInstalledAcceptance,
    [switch]$RequireSignature,
    [string]$ElectronDownloadCache,
    [string]$ExpectedSignerThumbprint = $env:WINDOWS_EXPECTED_SIGNER_THUMBPRINT
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $IsWindows) {
    throw "The EDGEOS NSIS installer must be built on Windows."
}
if ($PayloadOnly -and $PackageOnly) {
    throw "PayloadOnly and PackageOnly cannot be used together."
}
if ($PayloadOnly -and $RequireSignature) {
    throw "RequireSignature applies to a complete installer and cannot be combined with PayloadOnly."
}
if ($RequireSignature -and $SkipInstalledAcceptance) {
    throw "A signature-required release cannot skip the installed NSIS acceptance test."
}
if ($ElectronDownloadCache -and -not [IO.Path]::IsPathFullyQualified($ElectronDownloadCache)) {
    throw "ElectronDownloadCache must be an absolute directory path."
}

function Invoke-WithoutSensitiveBuildEnvironment {
    param([Parameter(Mandatory = $true)] [scriptblock]$Action)
    $names = @(
        "CSC_LINK", "CSC_KEY_PASSWORD", "WIN_CSC_LINK", "WIN_CSC_KEY_PASSWORD",
        "WINDOWS_CERTIFICATE_BASE64", "WINDOWS_CERTIFICATE_PASSWORD"
    )
    $saved = @{}
    foreach ($name in $names) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name)
        [Environment]::SetEnvironmentVariable($name, $null)
    }
    try {
        & $Action
    } finally {
        foreach ($name in $names) {
            [Environment]::SetEnvironmentVariable($name, $saved[$name])
        }
    }
}

function Invoke-WithTemporaryAppData {
    param([Parameter(Mandatory = $true)] [scriptblock]$Action)

    $temporaryBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    $temporaryRoot = [System.IO.Path]::GetFullPath(
        (Join-Path $temporaryBase "edgeos-packaged-smoke-$([Guid]::NewGuid().ToString('N'))")
    )
    $expectedPrefix = $temporaryBase.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    ) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $temporaryRoot.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "The packaged-smoke user-data directory escaped the system temporary directory."
    }

    $temporaryAppData = Join-Path $temporaryRoot "AppData/Roaming"
    $temporaryLocalAppData = Join-Path $temporaryRoot "AppData/Local"
    $priorAppData = $env:APPDATA
    $priorLocalAppData = $env:LOCALAPPDATA
    try {
        New-Item -ItemType Directory -Path $temporaryAppData -Force | Out-Null
        New-Item -ItemType Directory -Path $temporaryLocalAppData -Force | Out-Null
        $env:APPDATA = $temporaryAppData
        $env:LOCALAPPDATA = $temporaryLocalAppData
        & $Action
    } finally {
        if ($null -eq $priorAppData) {
            Remove-Item Env:APPDATA -ErrorAction SilentlyContinue
        } else {
            $env:APPDATA = $priorAppData
        }
        if ($null -eq $priorLocalAppData) {
            Remove-Item Env:LOCALAPPDATA -ErrorAction SilentlyContinue
        } else {
            $env:LOCALAPPDATA = $priorLocalAppData
        }
        if (Test-Path -LiteralPath $temporaryRoot -PathType Container) {
            $temporaryItem = Get-Item -LiteralPath $temporaryRoot -Force -ErrorAction SilentlyContinue
            if ($null -ne $temporaryItem -and
                -not ($temporaryItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
                Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
Push-Location $repositoryRoot
try {
    $forbiddenDesktopSecrets = @(
        "SUPABASE_SECRET_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_JWT_SECRET",
        "DATABASE_URL",
        "EDGEOS_CREDENTIAL_MASTER_KEY",
        "THE_ODDS_API_KEY",
        "KALSHI_PRIVATE_KEY",
        "POLYMARKET_PRIVATE_KEY"
    )
    foreach ($secretName in $forbiddenDesktopSecrets) {
        $secretValue = [Environment]::GetEnvironmentVariable($secretName)
        if (-not [string]::IsNullOrWhiteSpace($secretValue)) {
            throw "$secretName is set. EDGEOS refuses to build a desktop installer while server/provider secrets are present. Use hosted ingestion and privileged services instead."
        }
    }

    # electron-builder accepts both the generic CSC_* variables and the
    # Windows-specific WIN_CSC_* aliases. Normalize the aliases before any
    # signing decision so the API, Electron executable, and installer can
    # never be signed inconsistently.
    $genericCertificate = $env:CSC_LINK
    $windowsCertificate = $env:WIN_CSC_LINK
    $genericPassword = $env:CSC_KEY_PASSWORD
    $windowsPassword = $env:WIN_CSC_KEY_PASSWORD
    if (-not [string]::IsNullOrWhiteSpace($genericCertificate) -and
        -not [string]::IsNullOrWhiteSpace($windowsCertificate) -and
        $genericCertificate -cne $windowsCertificate) {
        throw "CSC_LINK and WIN_CSC_LINK disagree. Supply one certificate path, or set both to the identical local PFX path."
    }
    if (-not [string]::IsNullOrWhiteSpace($genericPassword) -and
        -not [string]::IsNullOrWhiteSpace($windowsPassword) -and
        $genericPassword -cne $windowsPassword) {
        throw "CSC_KEY_PASSWORD and WIN_CSC_KEY_PASSWORD disagree. Supply one password variable, or set both identically."
    }
    $normalizedCertificate = if (-not [string]::IsNullOrWhiteSpace($genericCertificate)) {
        $genericCertificate
    } else {
        $windowsCertificate
    }
    $normalizedPassword = if (-not [string]::IsNullOrWhiteSpace($genericPassword)) {
        $genericPassword
    } else {
        $windowsPassword
    }
    if ([string]::IsNullOrWhiteSpace($normalizedCertificate) -and
        -not [string]::IsNullOrWhiteSpace($normalizedPassword)) {
        throw "A Windows signing password was supplied without CSC_LINK or WIN_CSC_LINK."
    }
    if (-not [string]::IsNullOrWhiteSpace($normalizedCertificate)) {
        if ([string]::IsNullOrWhiteSpace($normalizedPassword)) {
            throw "CSC_KEY_PASSWORD or WIN_CSC_KEY_PASSWORD is required when a signing certificate is supplied."
        }
        $env:CSC_LINK = $normalizedCertificate
        $env:CSC_KEY_PASSWORD = $normalizedPassword
    }

    $requiredCommands = @("node", "npm")
    if (-not $PackageOnly) { $requiredCommands += "uv" }
    foreach ($command in $requiredCommands) {
        if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
            throw "Required command '$command' is not available on PATH."
        }
    }

    if (-not $SkipDependencyInstall -and -not $PackageOnly) {
        & npm.cmd ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed with exit code $LASTEXITCODE." }

        Push-Location (Join-Path $repositoryRoot "apps/api")
        try {
            & uv sync --all-groups --locked --no-editable
            if ($LASTEXITCODE -ne 0) { throw "uv sync failed with exit code $LASTEXITCODE." }
        } finally {
            Pop-Location
        }
    }

    if (-not $PackageOnly) {
        $env:NEXT_TELEMETRY_DISABLED = "1"
        $env:NEXT_PUBLIC_API_URL = "/api/edgeos"
        $env:NEXT_PUBLIC_APP_MODE = "demo"
        $env:NEXT_PUBLIC_DESKTOP_BUILD = "true"

        & npm.cmd run build --workspace=@edgeos/web
        if ($LASTEXITCODE -ne 0) { throw "Next.js build failed with exit code $LASTEXITCODE." }

        $apiBuildScript = Join-Path $PSScriptRoot "build-windows-api.ps1"
        if (-not (Test-Path -LiteralPath $apiBuildScript -PathType Leaf)) {
            throw "API packaging script was not found: $apiBuildScript"
        }
        & $apiBuildScript -SkipDependencyInstall
        if ($LASTEXITCODE -ne 0) { throw "API packaging failed with exit code $LASTEXITCODE." }
    }

    $apiExecutable = Join-Path $repositoryRoot "apps/api/dist/edgeos-api.exe"
    $hasSigningCertificate = -not [string]::IsNullOrWhiteSpace($env:CSC_LINK)
    $signatureRequired = $RequireSignature -or $hasSigningCertificate
    if ($RequireSignature -and -not $hasSigningCertificate) {
        throw "RequireSignature was set, but CSC_LINK is unavailable."
    }
    if (-not [string]::IsNullOrWhiteSpace($ExpectedSignerThumbprint)) {
        $ExpectedSignerThumbprint = $ExpectedSignerThumbprint.Replace(" ", "").ToUpperInvariant()
        if ($ExpectedSignerThumbprint -notmatch '^[A-F0-9]{40}$') {
            throw "ExpectedSignerThumbprint must be a 40-character SHA-1 certificate thumbprint."
        }
        if (-not $hasSigningCertificate) {
            throw "ExpectedSignerThumbprint requires a signing certificate."
        }
    }
    if ($RequireSignature -and [string]::IsNullOrWhiteSpace($ExpectedSignerThumbprint)) {
        throw "RequireSignature also requires ExpectedSignerThumbprint (or WINDOWS_EXPECTED_SIGNER_THUMBPRINT) to pin the approved publisher."
    }
    if ($hasSigningCertificate) {
        if ([string]::IsNullOrWhiteSpace($env:CSC_KEY_PASSWORD)) {
            throw "CSC_KEY_PASSWORD is required when CSC_LINK is set."
        }
        if (-not (Test-Path -LiteralPath $env:CSC_LINK -PathType Leaf)) {
            throw "CSC_LINK must identify a local PFX file so the bundled API can also be signed."
        }
        & (Join-Path $PSScriptRoot "sign-windows-executable.ps1") `
            -Executable $apiExecutable `
            -CertificatePath $env:CSC_LINK `
            -CertificatePassword $env:CSC_KEY_PASSWORD
        if ($LASTEXITCODE -ne 0) { throw "API signing failed with exit code $LASTEXITCODE." }
    }

    if (-not $PackageOnly -or -not [string]::IsNullOrWhiteSpace($env:CSC_LINK)) {
        & (Join-Path $PSScriptRoot "stage-windows-resources.ps1")
        if ($LASTEXITCODE -ne 0) { throw "Desktop resource staging failed with exit code $LASTEXITCODE." }

        Invoke-WithoutSensitiveBuildEnvironment {
            & (Join-Path $PSScriptRoot "test-windows-runtime.ps1")
            if ($LASTEXITCODE -ne 0) { throw "Packaged runtime smoke test failed with exit code $LASTEXITCODE." }
        }
    }

    if (-not $SkipTests -and -not $PackageOnly) {
        & npm.cmd run typecheck --workspace=@edgeos/desktop
        if ($LASTEXITCODE -ne 0) { throw "Desktop typecheck failed with exit code $LASTEXITCODE." }
        & npm.cmd run test --workspace=@edgeos/desktop
        if ($LASTEXITCODE -ne 0) { throw "Desktop tests failed with exit code $LASTEXITCODE." }
    }

    if ($PayloadOnly) {
        Write-Host "Windows payload build and runtime smoke tests completed; NSIS packaging was deferred."
        return
    }

    if (-not $hasSigningCertificate) {
        $env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
        Write-Host "No Windows signing certificate was supplied; producing an unsigned installer."
    } else {
        Write-Host "A Windows signing certificate was supplied; Authenticode signing is required."
    }

    $releaseDirectory = Join-Path $repositoryRoot "apps/desktop/release"
    if (Test-Path -LiteralPath $releaseDirectory) {
        # electron-builder output is generated; clear it so stale installers can
        # never be mistaken for the artifact produced by this invocation.
        if ((Get-Item -LiteralPath $releaseDirectory -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "Refusing to replace a reparse-point release directory: $releaseDirectory"
        }
        Remove-Item -LiteralPath $releaseDirectory -Recurse -Force
    }
    $packagingArguments = @("run", "dist:win", "--workspace=@edgeos/desktop")
    if ($ElectronDownloadCache) {
        $packagingArguments += @("--", "--config.electronDownload.cache=$ElectronDownloadCache")
    }
    & npm.cmd @packagingArguments
    if ($LASTEXITCODE -ne 0) { throw "NSIS packaging failed with exit code $LASTEXITCODE." }

    $verifyArguments = @{}
    if ($signatureRequired) { $verifyArguments.RequireSignature = $true }
    if (-not [string]::IsNullOrWhiteSpace($ExpectedSignerThumbprint)) {
        $verifyArguments.ExpectedSignerThumbprint = $ExpectedSignerThumbprint
    }
    & (Join-Path $PSScriptRoot "verify-windows-installer.ps1") @verifyArguments
    if ($LASTEXITCODE -ne 0) { throw "Installer verification failed with exit code $LASTEXITCODE." }

    # Inspect signatures, hashes, the exact packaged payload, and Electron fuses
    # before launching any generated executable on the build host.
    Invoke-WithoutSensitiveBuildEnvironment {
        Invoke-WithTemporaryAppData {
            & (Join-Path $PSScriptRoot "test-packaged-windows-app.ps1")
            if ($LASTEXITCODE -ne 0) { throw "Packaged Electron smoke test failed with exit code $LASTEXITCODE." }
        }
    }
    if ($SkipInstalledAcceptance) {
        Write-Warning "Installed NSIS acceptance was skipped; this output is not a fully acceptance-tested release candidate."
    } else {
        Invoke-WithoutSensitiveBuildEnvironment {
            & (Join-Path $PSScriptRoot "test-windows-installer-install.ps1")
        }
    }
} finally {
    Pop-Location
}
