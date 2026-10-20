#requires -Version 7.0
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $IsWindows) {
    throw "Run BUILD-EDGEOS.cmd on your Windows PC. This is not a cross-compiler."
}
if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne
    [System.Runtime.InteropServices.Architecture]::X64) {
    throw "This first installer targets x64 Windows. ARM Windows is not verified."
}

$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
foreach ($requiredFile in @("package-lock.json", "apps/api/uv.lock", "scripts/build-windows-installer.ps1", "scripts/windows-build-storage.ps1", "scripts/run-web-tests.mjs")) {
    if (-not (Test-Path -LiteralPath (Join-Path $repositoryRoot $requiredFile) -PathType Leaf)) {
        throw "Extract the entire EDGEOS source ZIP before running this launcher. Missing: $requiredFile"
    }
}
. (Join-Path $PSScriptRoot "windows-build-storage.ps1")
$buildStorage = Get-EdgeosBuildStorageEnvironment -RepositoryRoot $repositoryRoot
$buildDrive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($repositoryRoot))
if (-not $buildDrive.IsReady) { throw "The drive containing the extracted source is not ready." }
$freeGiB = [math]::Round($buildDrive.AvailableFreeSpace / 1GB, 1)
# A conservative preflight guard, not a measured maximum build size. Actual
# requirements vary with dependencies and existing caches; retain extra headroom.
if ($buildDrive.AvailableFreeSpace -lt 8GB) {
    throw "Only $freeGiB GiB free on $($buildDrive.Name). Extract a fresh source ZIP on a drive with at least 8 GiB free (15 GiB recommended). Existing files were not deleted."
}

$missing = @("node.exe", "npm.cmd", "uv.exe") | Where-Object {
    -not (Get-Command $_ -CommandType Application -ErrorAction SilentlyContinue)
}
if (@($missing).Count -gt 0) {
    Write-Host "Missing build tools: $($missing -join ', ')"
    Write-Host "Install the required tools in Windows Terminal, then reopen BUILD-EDGEOS.cmd:"
    Write-Host "  winget install --exact --id OpenJS.NodeJS.22 --source winget"
    Write-Host "  winget install --exact --id astral-sh.uv --source winget"
    throw "Prerequisites are incomplete. No build was started."
}
$nodeMajor = & node.exe -p "process.versions.node.split('.')[0]"
if ($LASTEXITCODE -ne 0 -or [int]$nodeMajor -lt 22) {
    throw "Node.js 22 or newer is required; Node.js 22 is the CI-tested build version."
}

# Protect an existing installation before spending time on the build. Acceptance
# briefly installs its generated artifact and owns the EDGEOS shortcut names.
$registryLocations = @(
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
)
$existingInstalls = @(Get-ItemProperty -Path $registryLocations -ErrorAction SilentlyContinue |
    Where-Object {
        $displayName = $_.PSObject.Properties["DisplayName"]
        $null -ne $displayName -and $displayName.Value -eq "EDGEOS"
    })
if ($existingInstalls.Count -gt 0) {
    throw "EDGEOS is already installed. Use a clean Windows test account/VM for the install-and-uninstall acceptance test. This launcher will not remove your installation."
}

Write-Host ""
Write-Host "EDGEOS local Windows build"
Write-Host "This uses your computer, not paid GitHub Actions minutes."
Write-Host "Internet is required to download locked dependencies, Python 3.12, Electron, and packaging tools."
Write-Host "Python is installed as a regular package for packaging, not an editable development install."
Write-Host "If a package host remains unreachable, stop and review the network error; security checks stay enabled."
Write-Host "It will run tests, build the app, briefly install/launch/uninstall it for acceptance, and keep the installer."
Write-Host "It does not deploy live services, place trades, change billing, or register a background build runner."
Write-Host "Without your own signing certificate, the installer will be unsigned development output."
Write-Host "Build folder: $repositoryRoot"
Write-Host "Build drive: $($buildDrive.Name) ($freeGiB GiB free; 15 GiB recommended)"
Write-Host "Downloads and temporary files: $(Join-Path $repositoryRoot '.edgeos-build-cache')"
Write-Host "These locations apply only to this build process; existing caches are not moved or deleted."
Write-Host "Keep some free space on the Windows system drive for Windows and installed tools."
Write-Host ""
if ((Read-Host "Type BUILD to continue, or press Enter to cancel") -cne "BUILD") {
    Write-Host "Canceled. No dependencies installed or build started."
    exit 0
}

function Invoke-CheckedNative {
    param(
        [Parameter(Mandatory = $true)] [string]$Command,
        [Parameter(Mandatory = $true)] [string[]]$Arguments
    )
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE. See the preceding output."
    }
}

$logDirectory = Join-Path $repositoryRoot "logs"
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$logName = "windows-build-$([DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss'))-$([Guid]::NewGuid().ToString('N')).log"
$logPath = Join-Path $logDirectory $logName
$webTestReportPath = [IO.Path]::ChangeExtension($logPath, "web-tests.json")
$transcriptStarted = $false
$buildSucceeded = $false
Push-Location $repositoryRoot
try {
    Start-Transcript -LiteralPath $logPath | Out-Null
    $transcriptStarted = $true
    Invoke-WithEdgeosBuildStorage -RepositoryRoot $repositoryRoot -Action {
        Write-Host "Build download/cache/temp directories are on $($buildDrive.Name)."
        Invoke-CheckedNative "npm.cmd" @("ci", "--no-audit", "--no-fund")
        # Diagnose the previously failing web stage before the Python checks and
        # expensive native packaging. This does not skip any later validation.
        Invoke-CheckedNative "node.exe" @((Join-Path $PSScriptRoot "run-web-tests.mjs"), $webTestReportPath)
        if (-not (Test-Path -LiteralPath $webTestReportPath -PathType Leaf)) {
            throw "Web tests returned without their required verification report."
        }
        foreach ($check in @("lint", "typecheck")) {
            Invoke-CheckedNative "npm.cmd" @("run", $check, "--workspace=@edgeos/web")
        }
        Invoke-CheckedNative "uv.exe" @("python", "install", "3.12")
        Push-Location (Join-Path $repositoryRoot "apps/api")
        try {
            Invoke-CheckedNative "uv.exe" @("sync", "--all-groups", "--locked", "--no-editable")
            Invoke-CheckedNative "uv.exe" @("run", "--locked", "--no-editable", "ruff", "check", ".")
            Invoke-CheckedNative "uv.exe" @("run", "--locked", "--no-editable", "mypy", "edgeos")
            Invoke-CheckedNative "uv.exe" @("run", "--locked", "--no-editable", "pytest")
        } finally {
            Pop-Location
        }
        # The builder's own Electron ZIP download needs explicit config in
        # addition to electron_config_cache and ELECTRON_BUILDER_CACHE.
        & (Join-Path $PSScriptRoot "build-windows-installer.ps1") -SkipDependencyInstall `
            -ElectronDownloadCache $buildStorage["electron_config_cache"]
        if ($LASTEXITCODE -ne 0) { throw "Windows packaging or acceptance failed." }
    }

    $outputDirectory = Join-Path $repositoryRoot "artifacts/windows"
    $manifestPath = Join-Path $outputDirectory "windows-build-manifest.json"
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $installerName = [string]$manifest.installer
    if ($installerName -notmatch '^EDGEOS Setup [0-9A-Za-z.+-]+\.exe$') {
        throw "The generated manifest has an invalid installer filename."
    }
    $installerPath = Join-Path $outputDirectory $installerName
    if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
        throw "The build returned without the actual installer file."
    }
    $installer = Get-Item -LiteralPath $installerPath
    $hash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($installer.Length -ne [long]$manifest.installerBytes -or
        $hash -cne [string]$manifest.installerSha256) {
        throw "The final installer does not match its build manifest."
    }
    $buildSucceeded = $true
    Write-Host ""
    Write-Host "BUILD AND AUTOMATED WINDOWS ACCEPTANCE PASSED"
    Write-Host "Installer: $installerPath"
    Write-Host "Bytes: $($installer.Length)"
    Write-Host "SHA-256: $hash"
    Write-Host "Signature status: $($manifest.signatureStatus)"
    Write-Host "Unsigned output is not an authenticated-publisher production release."
    Write-Host "This verification does not prove a configured live market opportunity."
    Write-Host "Review Windows security warnings; do not disable antivirus or organization controls."
} catch {
    Write-Host ""
    Write-Host "BUILD FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Do not treat any intermediate installer as acceptance-tested."
    $failureSummaryPath = "$webTestReportPath.failure.txt"
    if (Test-Path -LiteralPath $failureSummaryPath -PathType Leaf) {
        Write-Host (Get-Content -LiteralPath $failureSummaryPath -Raw)
    }
} finally {
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
    Pop-Location
}
Write-Host "Build log: $logPath"
Write-Host "Review logs for personal paths or credentials before sharing them."
if (-not $buildSucceeded) { exit 1 }
try {
    Start-Process explorer.exe -ArgumentList ('"{0}"' -f $outputDirectory)
} catch {
    Write-Warning "The build succeeded, but Explorer could not open. Open the installer folder printed above."
}
exit 0
