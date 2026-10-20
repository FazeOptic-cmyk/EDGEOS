#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Get-Location).Path,
    [switch]$PatchOnly,
    [switch]$RequireSignature,
    [string]$ExpectedSignerThumbprint = $env:WINDOWS_EXPECTED_SIGNER_THUMBPRINT
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if (-not $PatchOnly -and -not $IsWindows) {
    throw "Installer repair and acceptance must run on Windows. PatchOnly can be used to check the source repair elsewhere."
}
if (-not [IO.Path]::IsPathFullyQualified($RepositoryRoot)) {
    throw "RepositoryRoot must be the absolute path to the extracted EDGEOS source folder."
}
$RepositoryRoot = [IO.Path]::GetFullPath($RepositoryRoot)

function Get-TextSha256 {
    param([Parameter(Mandatory = $true)] [string]$Text)
    $bytes = [Text.Encoding]::UTF8.GetBytes($Text.Replace("`r`n", "`n"))
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Assert-RegularPath {
    param([Parameter(Mandatory = $true)] [string]$Path)
    # Check ancestors as well so backups and edits cannot follow a directory link.
    $current = $Path
    while (-not [string]::IsNullOrEmpty($current)) {
        if (Test-Path -LiteralPath $current) {
            if ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Refusing to repair through a linked path: $current"
            }
        }
        $current = [IO.Path]::GetDirectoryName($current)
    }
}

$verifierPath = Join-Path $RepositoryRoot "apps/desktop/scripts/verify-electron-fuses.mjs"
$asarVerifierPath = Join-Path $RepositoryRoot "apps/desktop/scripts/verify-packaged-asar.mjs"
$configPath = Join-Path $RepositoryRoot "apps/desktop/electron-builder.yml"
$packagePath = Join-Path $RepositoryRoot "apps/desktop/package.json"
$builderPath = Join-Path $RepositoryRoot "scripts/build-windows-installer.ps1"
$storagePath = Join-Path $RepositoryRoot "scripts/windows-build-storage.ps1"
foreach ($required in @($verifierPath, $asarVerifierPath, $configPath, $packagePath, $builderPath, $storagePath)) {
    Assert-RegularPath $required
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "The extracted source is missing: $required"
    }
}
$desktopPackage = Get-Content -LiteralPath $packagePath -Raw | ConvertFrom-Json
if ($desktopPackage.name -cne "@edgeos/desktop" -or
    $desktopPackage.devDependencies.electron -cne "44.8.0" -or
    $desktopPackage.devDependencies.'@electron/fuses' -cne "1.8.0" -or
    $desktopPackage.devDependencies.'electron-builder' -cne "26.15.3") {
    throw "This repair is for the EDGEOS source with Electron 44.8.0, @electron/fuses 1.8.0, and electron-builder 26.15.3."
}

# Accept the known affected source and this repair's result, allowing CRLF or LF.
# Unknown edits must be reviewed rather than overwritten by a repair download.
$edits = @(
    @{
        Path = $verifierPath
        OldHash = "a6ce45ecfd074714f41c425beb9111dfa5bf97957736cc40f9a3ff123b6a9950"
        NewHash = "4b2fdcaedb0ab90a33340c6eb60b3da2af95d8cfe6c437230d49ce38dd98f9f5"
    },
    @{
        Path = $configPath
        OldHash = "b05c0e00a155b0c1c897501d2d6c47c570cb0a795c00bc45eacc80adbb8c6394"
        PriorHash = "c1e822b6a5a6c78c796e58308d653a26a77cad8b8ae6a10da8bf8b180620827f"
        NewHash = "0dab1c7d99b78051aaff0b161c8326ba7e099d23b3550144b4c7327ba675a575"
    },
    @{
        Path = $asarVerifierPath
        OldHash = "7698af51548f34c0950dbe9db51d9123739a0ad436a4c4ec5f3d5ef06daff3d6"
        PriorHash = "c2e9b23edd3fbc7ccc87dafcbe7f18f2d3454e13eb2192b43309351358b4bfd1"
        NewHash = "973f9a18f8cae72b3b011f66b3c2b7f0593f3d6562fc2ebfd92803676bdd83b7"
    }
)
foreach ($edit in $edits) {
    $edit.Original = [IO.File]::ReadAllText($edit.Path)
    $normalized = $edit.Original.Replace("`r`n", "`n")
    $hash = Get-TextSha256 $normalized
    $acceptedHashes = @($edit.OldHash, $edit.NewHash)
    if ($edit.ContainsKey("PriorHash")) { $acceptedHashes += $edit.PriorHash }
    if ($hash -cnotin $acceptedHashes) {
        throw "This file differs from the supported source or repaired result; nothing was changed: $($edit.Path)"
    }
    $edit.Changed = $hash -cne $edit.NewHash
    if ($edit.Changed) {
        if ($edit.Path -ceq $verifierPath) {
            $normalized = $normalized.Replace('const expected = new Map([', "const wasmTrapHandlers = FuseV1Options.WasmTrapHandlers ?? 8;`nconst expected = new Map([")
            $normalized = $normalized.Replace('[FuseV1Options.LoadBrowserProcessSpecificV8Snapshot, enabled]', '[FuseV1Options.LoadBrowserProcessSpecificV8Snapshot, disabled]')
            $normalized = $normalized.Replace('  [FuseV1Options.GrantFileProtocolExtraPrivileges, disabled],', "  [FuseV1Options.GrantFileProtocolExtraPrivileges, disabled],`n  [wasmTrapHandlers, enabled],")
            $normalized = $normalized.Replace('${FuseV1Options[option]}', '${FuseV1Options[option] ?? (option === wasmTrapHandlers ? "WasmTrapHandlers" : option)}')
            $normalized = $normalized.Replace('console.log("Verified all eight Electron fuse states in the packaged executable.");', 'console.log(`Verified all ${expected.size} Electron fuse states in the packaged executable.`);')
        } elseif ($edit.Path -ceq $asarVerifierPath) {
            $normalized = $normalized.Replace('extractFile(archive, "dist/src/resource-integrity.json")', 'extractFile(archive, ["dist", "src", "resource-integrity.json"].join(sep))')
            $oldMismatch = '    throw new Error("Packaged web/API resource set does not exactly match the ASAR integrity manifest");'
            $newMismatch = @'
    const missing = [...declaredPaths].filter((path) => !actualPaths.has(path)).sort();
    const unexpected = [...actualPaths].filter((path) => !declaredPaths.has(path)).sort();
    const describe = (label, paths) => `${label} (${paths.length}): ${JSON.stringify(paths.slice(0, 20))}${paths.length > 20 ? " (first 20 shown)" : ""}`;
    throw new Error([
      "Packaged web/API resource set does not exactly match the ASAR integrity manifest",
      `Declared files: ${declaredPaths.size}; packaged files: ${actualPaths.size}.`,
      describe("Missing", missing),
      describe("Unexpected", unexpected),
    ].join("\n"));
'@
            $normalized = $normalized.Replace($oldMismatch, $newMismatch.Replace("`r`n", "`n"))
        } else {
            $normalized = $normalized.Replace('  loadBrowserProcessSpecificV8Snapshot: true', '  loadBrowserProcessSpecificV8Snapshot: false')
            $oldWebCopy = "  - from: resources/web`n    to: web`n    filter:`n      - `"**/*`""
            $newWebCopy = "  - from: resources`n    to: .`n    filter:`n      - `"web/**/*`""
            $normalized = $normalized.Replace($oldWebCopy, $newWebCopy)
        }
    }
    if ((Get-TextSha256 $normalized) -cne $edit.NewHash) {
        throw "The prepared repair did not match its expected result; nothing was changed."
    }
    $edit.Updated = if ($edit.Original.Contains("`r`n")) { $normalized.Replace("`n", "`r`n") } else { $normalized }
}

. $storagePath
$buildStorage = Get-EdgeosBuildStorageEnvironment -RepositoryRoot $RepositoryRoot
if (-not $PatchOnly) {
    foreach ($command in @("node.exe", "npm.cmd")) {
        if (-not (Get-Command $command -CommandType Application -ErrorAction SilentlyContinue)) {
            throw "Required build command is unavailable: $command"
        }
    }
    foreach ($relativePath in @("node_modules/.bin/electron-builder.cmd", "apps/api/dist/edgeos-api.exe", "apps/desktop/resources/api/edgeos-api.exe", "apps/desktop/resources/web/server.js")) {
        if (-not (Test-Path -LiteralPath (Join-Path $RepositoryRoot $relativePath) -PathType Leaf)) {
            throw "The previous build payload is missing $relativePath. This repair cannot resume that source folder."
        }
    }
    $registrations = @(
        Get-ItemProperty -Path @(
            "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
            "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
            "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
        ) -ErrorAction SilentlyContinue | Where-Object {
            $displayName = $_.PSObject.Properties["DisplayName"]
            $null -ne $displayName -and $displayName.Value -eq "EDGEOS"
        }
    )
    if ($registrations.Count -ne 0) {
        throw "EDGEOS is already installed. Use a clean Windows test account or VM for installed acceptance; this repair will not remove your installation."
    }
    Write-Host "Source folder: $RepositoryRoot"
    Write-Host "This backs up and repairs the Electron fuses, ASAR lookup/diagnostics, and web dependency copy rule, then repackages the existing staged payload."
    Write-Host "It replaces generated apps/desktop/release and artifacts/windows output, and runs the existing verification and install/launch/uninstall acceptance checks."
    Write-Host "Dependencies, the web build, and the Python API build are reused. Test user data stays isolated by the existing build scripts."
    if ((Read-Host "Type REPAIR to continue, or press Enter to cancel") -cne "REPAIR") {
        Write-Host "Canceled. No files changed."
        return
    }
}

$logDirectory = Join-Path $RepositoryRoot "logs"
Assert-RegularPath $logDirectory
$repairDirectory = Join-Path $logDirectory "electron-fuse-repair-$([DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss'))-$([Guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $repairDirectory -Force | Out-Null
$transcriptPath = Join-Path $repairDirectory "repair.log"
$transcriptStarted = $false
Push-Location $RepositoryRoot
try {
    Start-Transcript -LiteralPath $transcriptPath | Out-Null
    $transcriptStarted = $true
    # Back up all original files before any edit, preserving their exact bytes.
    foreach ($edit in $edits) {
        Copy-Item -LiteralPath $edit.Path -Destination (Join-Path $repairDirectory ([IO.Path]::GetFileName($edit.Path)))
    }
    foreach ($edit in $edits) {
        if ($edit.Changed) {
            [IO.File]::WriteAllText($edit.Path, $edit.Updated, [Text.UTF8Encoding]::new($false))
        }
    }
    Write-Host "Source repair ready: nine fuses checked; optional snapshot disabled; native ASAR paths; web dependencies included; exact resource mismatch diagnostics enabled."
    Write-Host "Original files preserved in: $repairDirectory"
    if ($PatchOnly) {
        Write-Host "PatchOnly completed. No installer was built or acceptance-tested."
        return
    }

    Invoke-WithEdgeosBuildStorage -RepositoryRoot $RepositoryRoot -Action {
        $buildArguments = @{ PackageOnly = $true; ElectronDownloadCache = $buildStorage["electron_config_cache"] }
        if ($RequireSignature) { $buildArguments.RequireSignature = $true }
        if (-not [string]::IsNullOrWhiteSpace($ExpectedSignerThumbprint)) {
            $buildArguments.ExpectedSignerThumbprint = $ExpectedSignerThumbprint
        }
        $global:LASTEXITCODE = 0
        & $builderPath @buildArguments
        if ($LASTEXITCODE -ne 0) { throw "Windows packaging or acceptance failed with exit code $LASTEXITCODE." }
    }
    $outputDirectory = Join-Path $RepositoryRoot "artifacts/windows"
    $manifestPath = Join-Path $outputDirectory "windows-build-manifest.json"
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $installerName = [string]$manifest.installer
    if ($installerName -notmatch '^EDGEOS Setup [0-9A-Za-z.+-]+\.exe$') {
        throw "The generated manifest has an invalid installer filename."
    }
    $installerPath = Join-Path $outputDirectory $installerName
    $installer = Get-Item -LiteralPath $installerPath
    $hash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($installer.Length -ne [long]$manifest.installerBytes -or $hash -cne [string]$manifest.installerSha256) {
        throw "The final installer does not match its build manifest."
    }
    Write-Host "REPAIR, REPACKAGING, AND AUTOMATED WINDOWS ACCEPTANCE PASSED"
    Write-Host "Installer: $installerPath"
    Write-Host "Bytes: $($installer.Length)"
    Write-Host "SHA-256: $hash"
    Write-Host "Signature status: $($manifest.signatureStatus)"
} catch {
    Write-Host "REPAIR/BUILD FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Original source files remain in: $repairDirectory"
    Write-Host "Do not treat an intermediate installer as acceptance-tested."
    throw
} finally {
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
    Pop-Location
    Write-Host "Repair log: $transcriptPath"
}
