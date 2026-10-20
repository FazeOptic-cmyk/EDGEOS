[CmdletBinding()]
param(
    [string]$ReleaseDirectory,
    [string]$OutputDirectory,
    [switch]$RequireSignature,
    [string]$ExpectedSignerThumbprint
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($ReleaseDirectory)) {
    $ReleaseDirectory = Join-Path $repositoryRoot "apps/desktop/release"
}
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $repositoryRoot "artifacts/windows"
}
$ReleaseDirectory = [System.IO.Path]::GetFullPath($ReleaseDirectory)
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$expectedOutputDirectory = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot "artifacts/windows"))
if (-not $OutputDirectory.Equals($expectedOutputDirectory, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDirectory must be the dedicated generated directory: $expectedOutputDirectory"
}
if (-not [string]::IsNullOrWhiteSpace($ExpectedSignerThumbprint)) {
    $ExpectedSignerThumbprint = $ExpectedSignerThumbprint.Replace(" ", "").ToUpperInvariant()
    if ($ExpectedSignerThumbprint -notmatch '^[A-F0-9]{40}$') {
        throw "ExpectedSignerThumbprint must be a 40-character SHA-1 certificate thumbprint."
    }
}
if ($RequireSignature -and [string]::IsNullOrWhiteSpace($ExpectedSignerThumbprint)) {
    throw "RequireSignature also requires ExpectedSignerThumbprint to pin the approved publisher."
}
$signatureRequired = $RequireSignature -or -not [string]::IsNullOrWhiteSpace($ExpectedSignerThumbprint)

function Test-WindowsPeHeader {
    param([Parameter(Mandatory = $true)] [string]$Path)
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        return $stream.ReadByte() -eq 0x4D -and $stream.ReadByte() -eq 0x5A
    } finally {
        $stream.Dispose()
    }
}

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
if ($installer.Length -lt 1MB) {
    throw "Installer is unexpectedly small ($($installer.Length) bytes): $($installer.FullName)"
}

if (-not (Test-WindowsPeHeader -Path $installer.FullName)) {
    throw "Installer does not have a Windows PE header: $($installer.FullName)"
}

$signature = Get-AuthenticodeSignature -LiteralPath $installer.FullName
if ($signatureRequired -and $signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
    throw "A signed installer was required, but Authenticode status is '$($signature.Status)'."
}
if (-not $signatureRequired -and
    $signature.Status -notin @(
        [System.Management.Automation.SignatureStatus]::Valid,
        [System.Management.Automation.SignatureStatus]::NotSigned
    )) {
    throw "Installer has an unexpected Authenticode status: $($signature.Status)"
}

$stagedApiExecutable = Join-Path $repositoryRoot "apps/desktop/resources/api/edgeos-api.exe"
if (-not (Test-Path -LiteralPath $stagedApiExecutable -PathType Leaf)) {
    throw "The staged API executable was not found: $stagedApiExecutable"
}
$apiSignature = Get-AuthenticodeSignature -LiteralPath $stagedApiExecutable
if ($signatureRequired -and $apiSignature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
    throw "The bundled API must be signed, but Authenticode status is '$($apiSignature.Status)'."
}
if (-not $signatureRequired -and
    $apiSignature.Status -notin @(
        [System.Management.Automation.SignatureStatus]::Valid,
        [System.Management.Automation.SignatureStatus]::NotSigned
    )) {
    throw "Bundled API has an unexpected Authenticode status: $($apiSignature.Status)"
}
$apiHash = (Get-FileHash -LiteralPath $stagedApiExecutable -Algorithm SHA256).Hash.ToLowerInvariant()

$unpackedDirectory = Join-Path $ReleaseDirectory "win-unpacked"
$desktopExecutable = Join-Path $unpackedDirectory "EDGEOS.exe"
$packagedApiExecutable = Join-Path $unpackedDirectory "resources/api/edgeos-api.exe"
$packagedWebServer = Join-Path $unpackedDirectory "resources/web/server.js"
$packagedAsar = Join-Path $unpackedDirectory "resources/app.asar"
foreach ($packagedFile in @($desktopExecutable, $packagedApiExecutable, $packagedWebServer, $packagedAsar)) {
    if (-not (Test-Path -LiteralPath $packagedFile -PathType Leaf)) {
        throw "Expected packaged runtime file was not found: $packagedFile"
    }
}
& node (Join-Path $repositoryRoot "apps/desktop/scripts/verify-electron-fuses.mjs") $desktopExecutable
if ($LASTEXITCODE -ne 0) {
    throw "Packaged Electron fuse verification failed with exit code $LASTEXITCODE."
}
& node (Join-Path $repositoryRoot "apps/desktop/scripts/verify-packaged-asar.mjs") $packagedAsar (Join-Path $unpackedDirectory "resources")
if ($LASTEXITCODE -ne 0) {
    throw "Packaged ASAR resource-integrity verification failed with exit code $LASTEXITCODE."
}
$packagedApiHash = (Get-FileHash -LiteralPath $packagedApiExecutable -Algorithm SHA256).Hash.ToLowerInvariant()
$desktopSignature = Get-AuthenticodeSignature -LiteralPath $desktopExecutable
$packagedApiSignature = Get-AuthenticodeSignature -LiteralPath $packagedApiExecutable
if ($packagedApiHash -ne $apiHash) {
    throw "The API executable inside win-unpacked differs from the signed and hashed staged executable."
}
if ($signatureRequired -and $desktopSignature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
    throw "The packaged EDGEOS executable must be signed, but Authenticode status is '$($desktopSignature.Status)'."
}
if ($signatureRequired -and $packagedApiSignature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
    throw "The packaged API must be signed, but Authenticode status is '$($packagedApiSignature.Status)'."
}
if ($signatureRequired) {
    $requiredSignatures = @($signature, $desktopSignature, $packagedApiSignature)
    $signerThumbprints = @(
        $requiredSignatures |
            ForEach-Object { $_.SignerCertificate.Thumbprint } |
            Select-Object -Unique
    )
    if ($signerThumbprints.Count -ne 1) {
        throw "Installer, EDGEOS.exe, and edgeos-api.exe must use the same Authenticode signer."
    }
    if (-not [string]::IsNullOrWhiteSpace($ExpectedSignerThumbprint) -and
        $signerThumbprints[0].ToUpperInvariant() -ne $ExpectedSignerThumbprint) {
        throw "Authenticode signer thumbprint does not match the approved release signer."
    }
    foreach ($requiredSignature in $requiredSignatures) {
        if ($null -eq $requiredSignature.TimeStamperCertificate) {
            throw "A required Authenticode signature has no verified RFC 3161 timestamp."
        }
    }
}
if (-not $signatureRequired) {
    foreach ($runtimeStatus in @($desktopSignature.Status, $packagedApiSignature.Status)) {
        if ($runtimeStatus -notin @(
                [System.Management.Automation.SignatureStatus]::Valid,
                [System.Management.Automation.SignatureStatus]::NotSigned
            )) {
            throw "A packaged runtime executable has unexpected Authenticode status '$runtimeStatus'."
        }
    }
}

$blockmapPath = "$($installer.FullName).blockmap"
$latestMetadata = Join-Path $ReleaseDirectory "latest.yml"
if ((Test-Path -LiteralPath $latestMetadata -PathType Leaf) -and
    -not (Get-Content -LiteralPath $latestMetadata -Raw).Contains($installer.Name)) {
    throw "latest.yml does not reference the generated installer '$($installer.Name)'."
}

$artifactRoot = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot "artifacts"))
$pathComparison = if ($IsWindows) {
    [System.StringComparison]::OrdinalIgnoreCase
} else {
    [System.StringComparison]::Ordinal
}
if (-not $OutputDirectory.StartsWith($artifactRoot + [System.IO.Path]::DirectorySeparatorChar, $pathComparison)) {
    throw "Refusing to replace an artifact directory outside $artifactRoot"
}
if (Test-Path -LiteralPath $OutputDirectory) {
    if ((Get-Item -LiteralPath $OutputDirectory -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw "Refusing to replace a reparse-point artifact directory: $OutputDirectory"
    }
    Remove-Item -LiteralPath $OutputDirectory -Recurse -Force
}
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null

$filesToPublish = @($installer.FullName)
foreach ($optionalMetadata in @($blockmapPath, $latestMetadata)) {
    if (Test-Path -LiteralPath $optionalMetadata -PathType Leaf) {
        $filesToPublish += $optionalMetadata
    }
}
foreach ($sourceFile in $filesToPublish) {
    Copy-Item -LiteralPath $sourceFile -Destination $OutputDirectory -Force
}

$publishedInstaller = Join-Path $OutputDirectory $installer.Name
$installerHash = (Get-FileHash -LiteralPath $publishedInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
$buildManifest = [ordered]@{
    schemaVersion = 1
    product = "EDGEOS"
    platform = "windows"
    architecture = "x64"
    installer = $installer.Name
    installerBytes = $installer.Length
    installerSha256 = $installerHash
    signed = ($signature.Status -eq [System.Management.Automation.SignatureStatus]::Valid)
    signatureStatus = [string]$signature.Status
    stagedApiSha256 = $apiHash
    packagedApiSha256 = $packagedApiHash
    bundledApiSigned = ($apiSignature.Status -eq [System.Management.Automation.SignatureStatus]::Valid)
    bundledApiSignatureStatus = [string]$apiSignature.Status
    desktopExecutableSigned = ($desktopSignature.Status -eq [System.Management.Automation.SignatureStatus]::Valid)
    desktopExecutableSignatureStatus = [string]$desktopSignature.Status
    packagedApiSigned = ($packagedApiSignature.Status -eq [System.Management.Automation.SignatureStatus]::Valid)
    packagedApiSignatureStatus = [string]$packagedApiSignature.Status
    signerSubject = if ($null -eq $signature.SignerCertificate) { $null } else { $signature.SignerCertificate.Subject }
    signerThumbprint = if ($null -eq $signature.SignerCertificate) { $null } else { $signature.SignerCertificate.Thumbprint }
    expectedSignerThumbprint = if ([string]::IsNullOrWhiteSpace($ExpectedSignerThumbprint)) { $null } else { $ExpectedSignerThumbprint }
    timestamped = ($null -ne $signature.TimeStamperCertificate)
    sourceRevision = if ([string]::IsNullOrWhiteSpace($env:GITHUB_SHA)) { $null } else { $env:GITHUB_SHA }
}
$manifestPath = Join-Path $OutputDirectory "windows-build-manifest.json"
$buildManifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding utf8

$publishedFiles = @(Get-ChildItem -LiteralPath $OutputDirectory -File | Sort-Object Name)
$checksumLines = foreach ($file in $publishedFiles) {
    $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $($file.Name)"
}
$checksumPath = Join-Path $OutputDirectory "SHA256SUMS.txt"
[System.IO.File]::WriteAllLines(
    $checksumPath,
    $checksumLines,
    [System.Text.UTF8Encoding]::new($false)
)

Write-Host "Verified installer: $($installer.Name)"
Write-Host "Authenticode status: $($signature.Status)"
Write-Host "Bundled API Authenticode status: $($apiSignature.Status)"
Write-Host "Desktop executable Authenticode status: $($desktopSignature.Status)"
Write-Host "SHA-256: $installerHash"
Write-Host "Publishable artifacts: $OutputDirectory"
