[CmdletBinding()]
param(
    [string]$ReleaseDirectory,
    [int]$ProcessTimeoutSeconds = 120,
    [int]$RemovalTimeoutSeconds = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $IsWindows) {
    throw "The installed NSIS acceptance test must run on Windows."
}
if ($ProcessTimeoutSeconds -lt 30 -or $ProcessTimeoutSeconds -gt 600) {
    throw "ProcessTimeoutSeconds must be between 30 and 600."
}
if ($RemovalTimeoutSeconds -lt 5 -or $RemovalTimeoutSeconds -gt 120) {
    throw "RemovalTimeoutSeconds must be between 5 and 120."
}

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
. (Join-Path $PSScriptRoot "windows-nsis-arguments.ps1")
if ([string]::IsNullOrWhiteSpace($ReleaseDirectory)) {
    $ReleaseDirectory = Join-Path $repositoryRoot "apps/desktop/release"
}
$ReleaseDirectory = [System.IO.Path]::GetFullPath($ReleaseDirectory)
if (-not (Test-Path -LiteralPath $ReleaseDirectory -PathType Container)) {
    throw "Desktop release output was not found: $ReleaseDirectory"
}

$installers = @(
    Get-ChildItem -LiteralPath $ReleaseDirectory -File -Filter "EDGEOS Setup *.exe" |
        Where-Object { $_.DirectoryName -eq $ReleaseDirectory }
)
if ($installers.Count -ne 1) {
    throw "Expected exactly one EDGEOS Setup installer, found $($installers.Count)."
}
$installer = $installers[0]

$temporaryRoot = if ([string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) {
    [System.IO.Path]::GetTempPath()
} else {
    [System.IO.Path]::GetFullPath($env:RUNNER_TEMP)
}
$acceptanceRoot = Join-Path $temporaryRoot "edgeos-installed-acceptance-$([Guid]::NewGuid().ToString('N'))"
$installDirectory = Join-Path $acceptanceRoot "application"
$acceptanceRoot = [System.IO.Path]::GetFullPath($acceptanceRoot)
$installDirectory = [System.IO.Path]::GetFullPath($installDirectory)
$expectedPrefix = $acceptanceRoot.TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
) + [System.IO.Path]::DirectorySeparatorChar
if (-not $installDirectory.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "The generated installation directory escaped its dedicated temporary root."
}

$programsDirectory = [Environment]::GetFolderPath([Environment+SpecialFolder]::Programs)
$desktopDirectory = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
if ([string]::IsNullOrWhiteSpace($programsDirectory) -or [string]::IsNullOrWhiteSpace($desktopDirectory)) {
    throw "Windows did not provide current-user Start Menu and Desktop directories."
}
$startMenuShortcut = Join-Path $programsDirectory "EDGEOS.lnk"
$desktopShortcut = Join-Path $desktopDirectory "EDGEOS.lnk"
$acceptanceAppData = Join-Path $acceptanceRoot "profile/AppData/Roaming"
$acceptanceLocalAppData = Join-Path $acceptanceRoot "profile/AppData/Local"
$dataRoot = Join-Path $acceptanceAppData "EDGEOS"
$databasePath = Join-Path $dataRoot "data/edgeos-local.sqlite3"
$sentinelDirectory = Join-Path $dataRoot "acceptance-sentinels"
$sentinelPath = Join-Path $sentinelDirectory "installer-$([Guid]::NewGuid().ToString('N')).txt"
$sentinelValue = "EDGEOS NSIS retained-data proof $([Guid]::NewGuid().ToString('N'))"
$priorAppData = $env:APPDATA
$priorLocalAppData = $env:LOCALAPPDATA

foreach ($isolatedDirectory in @($acceptanceAppData, $acceptanceLocalAppData, $dataRoot)) {
    $isolatedFullPath = [System.IO.Path]::GetFullPath($isolatedDirectory)
    if (-not $isolatedFullPath.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "An isolated user-data directory escaped the dedicated acceptance root."
    }
}

function Get-EdgeosInstallRegistrations {
    $locations = @(
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    return @(
        Get-ItemProperty -Path $locations -ErrorAction SilentlyContinue |
            Where-Object {
                $displayName = $_.PSObject.Properties["DisplayName"]
                $null -ne $displayName -and $displayName.Value -eq "EDGEOS"
            }
    )
}

function Invoke-AcceptanceProcess {
    [CmdletBinding(DefaultParameterSetName = "ArgumentList")]
    param(
        [Parameter(Mandatory = $true)] [string]$Executable,
        [Parameter(Mandatory = $true, ParameterSetName = "ArgumentList")] [string[]]$ArgumentList,
        [Parameter(Mandatory = $true, ParameterSetName = "RawArguments")] [string]$RawArguments,
        [Parameter(Mandatory = $true)] [string]$Description
    )
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        throw "$Description executable was not found: $Executable"
    }
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Executable
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    if ($PSCmdlet.ParameterSetName -eq "RawArguments") {
        $startInfo.Arguments = $RawArguments
    } else {
        foreach ($argument in $ArgumentList) {
            [void]$startInfo.ArgumentList.Add($argument)
        }
    }
    $process = [System.Diagnostics.Process]::Start($startInfo)
    if ($null -eq $process) {
        throw "$Description did not create a process."
    }
    try {
        if (-not $process.WaitForExit($ProcessTimeoutSeconds * 1000)) {
            try { $process.Kill($true) } catch { }
            throw "$Description did not exit within $ProcessTimeoutSeconds seconds."
        }
        if ($process.ExitCode -ne 0) {
            throw "$Description exited with code $($process.ExitCode)."
        }
    } finally {
        $process.Dispose()
    }
}

function Wait-ForPathRemoval {
    param([Parameter(Mandatory = $true)] [string[]]$Paths)
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($RemovalTimeoutSeconds)
    do {
        $remaining = @($Paths | Where-Object { Test-Path -LiteralPath $_ })
        if ($remaining.Count -eq 0) { return }
        Start-Sleep -Milliseconds 250
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "The uninstaller did not remove: $($remaining -join ', ')"
}

function Assert-InstallRegistration {
    $registrations = @(Get-EdgeosInstallRegistrations)
    if ($registrations.Count -ne 1) {
        throw "Expected exactly one EDGEOS Windows uninstall registration, found $($registrations.Count)."
    }
    $uninstallCommand = [string]$registrations[0].UninstallString
    if ([string]::IsNullOrWhiteSpace($uninstallCommand) -or
        -not $uninstallCommand.Contains($installDirectory, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "The EDGEOS uninstall registration does not point at the acceptance installation directory."
    }
}

function Wait-ForEdgeosUnregistration {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($RemovalTimeoutSeconds)
    do {
        if (@(Get-EdgeosInstallRegistrations).Count -eq 0) { return }
        Start-Sleep -Milliseconds 250
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "The EDGEOS uninstall registration remained after silent uninstall."
}

function Get-ShortcutTarget {
    param([Parameter(Mandatory = $true)] [string]$ShortcutPath)
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $null
    try {
        $shortcut = $shell.CreateShortcut($ShortcutPath)
        return [System.IO.Path]::GetFullPath($shortcut.TargetPath)
    } finally {
        if ($null -ne $shortcut) {
            [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut)
        }
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell)
    }
}

function Assert-ShortcutTarget {
    param(
        [Parameter(Mandatory = $true)] [string]$ShortcutPath,
        [Parameter(Mandatory = $true)] [string]$ExpectedTarget
    )
    if (-not (Test-Path -LiteralPath $ShortcutPath -PathType Leaf)) {
        throw "Expected Windows shortcut was not created: $ShortcutPath"
    }
    $actualTarget = Get-ShortcutTarget -ShortcutPath $ShortcutPath
    $expected = [System.IO.Path]::GetFullPath($ExpectedTarget)
    if (-not $actualTarget.Equals($expected, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Shortcut '$ShortcutPath' targets '$actualTarget' instead of '$expected'."
    }
}

function Assert-InstalledPayload {
    param([Parameter(Mandatory = $true)] [string]$InstalledRoot)
    $comparisons = @(
        @{ RelativePath = "EDGEOS.exe"; Source = Join-Path $ReleaseDirectory "win-unpacked/EDGEOS.exe" },
        @{ RelativePath = "resources/api/edgeos-api.exe"; Source = Join-Path $ReleaseDirectory "win-unpacked/resources/api/edgeos-api.exe" },
        @{ RelativePath = "resources/app.asar"; Source = Join-Path $ReleaseDirectory "win-unpacked/resources/app.asar" }
    )
    foreach ($comparison in $comparisons) {
        $installedPath = Join-Path $InstalledRoot $comparison.RelativePath
        if (-not (Test-Path -LiteralPath $installedPath -PathType Leaf)) {
            throw "Installed payload file was not found: $installedPath"
        }
        if (-not (Test-Path -LiteralPath $comparison.Source -PathType Leaf)) {
            throw "Expected unpacked payload file was not found: $($comparison.Source)"
        }
        $installedHash = (Get-FileHash -LiteralPath $installedPath -Algorithm SHA256).Hash
        $sourceHash = (Get-FileHash -LiteralPath $comparison.Source -Algorithm SHA256).Hash
        if ($installedHash -ne $sourceHash) {
            throw "Installed '$($comparison.RelativePath)' differs from the verified packaged payload."
        }
    }
}

function Invoke-SilentInstall {
    Invoke-AcceptanceProcess `
        -Executable $installer.FullName `
        -RawArguments (Get-EdgeosNsisInstallArguments -InstallDirectory $installDirectory) `
        -Description "EDGEOS NSIS installer"
}

if (@(Get-EdgeosInstallRegistrations).Count -ne 0) {
    throw "Refusing to run installed acceptance while EDGEOS is already registered for this Windows account or machine. Use a clean test account or VM."
}
foreach ($preexistingShortcut in @($startMenuShortcut, $desktopShortcut)) {
    if (Test-Path -LiteralPath $preexistingShortcut) {
        throw "Refusing to overwrite a pre-existing EDGEOS shortcut: $preexistingShortcut"
    }
}
if (Test-Path -LiteralPath $acceptanceRoot) {
    throw "Refusing to replace an existing acceptance directory: $acceptanceRoot"
}

$installedExecutable = Join-Path $installDirectory "EDGEOS.exe"
$uninstaller = $null
$databaseHashBeforeUpgrade = $null
try {
    New-Item -ItemType Directory -Path $acceptanceRoot | Out-Null
    New-Item -ItemType Directory -Path $acceptanceAppData | Out-Null
    New-Item -ItemType Directory -Path $acceptanceLocalAppData | Out-Null
    # Electron derives its userData directory from APPDATA. Keep every
    # acceptance launch inside the unique test root so a developer's existing
    # EDGEOS workspace is never opened or changed by the release gate.
    $env:APPDATA = $acceptanceAppData
    $env:LOCALAPPDATA = $acceptanceLocalAppData
    Invoke-SilentInstall
    Assert-InstalledPayload -InstalledRoot $installDirectory
    Assert-InstallRegistration
    Assert-ShortcutTarget -ShortcutPath $startMenuShortcut -ExpectedTarget $installedExecutable
    Assert-ShortcutTarget -ShortcutPath $desktopShortcut -ExpectedTarget $installedExecutable

    & (Join-Path $PSScriptRoot "test-packaged-windows-app.ps1") `
        -UnpackedDirectory $installDirectory `
        -TimeoutSeconds ([Math]::Min($ProcessTimeoutSeconds, 300))
    if (-not (Test-Path -LiteralPath $databasePath -PathType Leaf)) {
        throw "Installed EDGEOS did not initialize its device-local SQLite workspace: $databasePath"
    }
    $databaseHashBeforeUpgrade = (Get-FileHash -LiteralPath $databasePath -Algorithm SHA256).Hash
    New-Item -ItemType Directory -Path $sentinelDirectory -Force | Out-Null
    [System.IO.File]::WriteAllText($sentinelPath, $sentinelValue, [System.Text.UTF8Encoding]::new($false))

    # Re-running the same version exercises electron-builder's installed
    # upgrade/reinstall path without fabricating a second release artifact.
    Invoke-SilentInstall
    Assert-InstalledPayload -InstalledRoot $installDirectory
    Assert-InstallRegistration
    Assert-ShortcutTarget -ShortcutPath $startMenuShortcut -ExpectedTarget $installedExecutable
    Assert-ShortcutTarget -ShortcutPath $desktopShortcut -ExpectedTarget $installedExecutable
    if (-not (Test-Path -LiteralPath $sentinelPath -PathType Leaf) -or
        (Get-Content -LiteralPath $sentinelPath -Raw) -ne $sentinelValue) {
        throw "The NSIS upgrade/reinstall path did not preserve the device-local data sentinel."
    }
    if (-not (Test-Path -LiteralPath $databasePath -PathType Leaf) -or
        (Get-FileHash -LiteralPath $databasePath -Algorithm SHA256).Hash -ne $databaseHashBeforeUpgrade) {
        throw "The NSIS upgrade/reinstall path changed or removed the device-local SQLite workspace before launch."
    }
    & (Join-Path $PSScriptRoot "test-packaged-windows-app.ps1") `
        -UnpackedDirectory $installDirectory `
        -TimeoutSeconds ([Math]::Min($ProcessTimeoutSeconds, 300))

    $uninstallers = @(Get-ChildItem -LiteralPath $installDirectory -File -Filter "Uninstall*.exe")
    if ($uninstallers.Count -ne 1) {
        throw "Expected exactly one installed EDGEOS uninstaller, found $($uninstallers.Count)."
    }
    $uninstaller = $uninstallers[0].FullName
    Invoke-AcceptanceProcess `
        -Executable $uninstaller `
        -ArgumentList @("/S") `
        -Description "EDGEOS NSIS uninstaller"
    Wait-ForPathRemoval -Paths @($installDirectory, $startMenuShortcut, $desktopShortcut)
    Wait-ForEdgeosUnregistration
    if (-not (Test-Path -LiteralPath $sentinelPath -PathType Leaf) -or
        (Get-Content -LiteralPath $sentinelPath -Raw) -ne $sentinelValue -or
        -not (Test-Path -LiteralPath $databasePath -PathType Leaf)) {
        throw "The uninstaller removed device-local user data despite the retention policy."
    }

    Write-Host "Installed NSIS acceptance passed: install, payload identity, shortcuts, launch, upgrade persistence, uninstall, and retained user data."
} finally {
    # Cleanup is constrained to the unique generated acceptance root. Retention
    # is verified before cleanup; no pre-existing EDGEOS user-data path is used.
    if (Test-Path -LiteralPath $installDirectory -PathType Container) {
        $remainingUninstallers = @(Get-ChildItem -LiteralPath $installDirectory -File -Filter "Uninstall*.exe" -ErrorAction SilentlyContinue)
        if ($remainingUninstallers.Count -eq 1) {
            try {
                Invoke-AcceptanceProcess `
                    -Executable $remainingUninstallers[0].FullName `
                    -ArgumentList @("/S") `
                    -Description "EDGEOS NSIS cleanup uninstaller"
            } catch { }
        }
    }
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
    Remove-Item -LiteralPath $sentinelPath -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $sentinelDirectory -PathType Container) {
        $sentinelChildren = @(Get-ChildItem -LiteralPath $sentinelDirectory -Force -ErrorAction SilentlyContinue)
        if ($sentinelChildren.Count -eq 0) {
            Remove-Item -LiteralPath $sentinelDirectory -Force -ErrorAction SilentlyContinue
        }
    }
    foreach ($possibleOwnedShortcut in @($startMenuShortcut, $desktopShortcut)) {
        if (Test-Path -LiteralPath $possibleOwnedShortcut -PathType Leaf) {
            try {
                $shortcutTarget = Get-ShortcutTarget -ShortcutPath $possibleOwnedShortcut
                if ($shortcutTarget.Equals(
                        [System.IO.Path]::GetFullPath($installedExecutable),
                        [System.StringComparison]::OrdinalIgnoreCase
                    )) {
                    Remove-Item -LiteralPath $possibleOwnedShortcut -Force
                }
            } catch { }
        }
    }
    if (@(Get-EdgeosInstallRegistrations).Count -eq 0 -and
        (Test-Path -LiteralPath $acceptanceRoot -PathType Container)) {
        $acceptanceItem = Get-Item -LiteralPath $acceptanceRoot -Force -ErrorAction SilentlyContinue
        if ($null -ne $acceptanceItem -and
            -not ($acceptanceItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            Remove-Item -LiteralPath $acceptanceRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}
