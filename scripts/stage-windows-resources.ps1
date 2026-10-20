[CmdletBinding()]
param(
    [string]$WebStandaloneDirectory,
    [string]$ApiExecutable,
    [string]$DestinationDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

if ([string]::IsNullOrWhiteSpace($WebStandaloneDirectory)) {
    $WebStandaloneDirectory = Join-Path $repositoryRoot "apps/web/.next/standalone"
}
if ([string]::IsNullOrWhiteSpace($ApiExecutable)) {
    $ApiExecutable = Join-Path $repositoryRoot "apps/api/dist/edgeos-api.exe"
}
if ([string]::IsNullOrWhiteSpace($DestinationDirectory)) {
    $DestinationDirectory = Join-Path $repositoryRoot "apps/desktop/resources"
}

$WebStandaloneDirectory = [System.IO.Path]::GetFullPath($WebStandaloneDirectory)
$ApiExecutable = [System.IO.Path]::GetFullPath($ApiExecutable)
$DestinationDirectory = [System.IO.Path]::GetFullPath($DestinationDirectory)
$desktopDirectory = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot "apps/desktop"))
$pathComparison = if ($IsWindows) {
    [System.StringComparison]::OrdinalIgnoreCase
} else {
    [System.StringComparison]::Ordinal
}
$expectedDestination = [System.IO.Path]::GetFullPath((Join-Path $desktopDirectory "resources"))
if (-not $DestinationDirectory.Equals($expectedDestination, $pathComparison)) {
    throw "DestinationDirectory must be the dedicated generated directory: $expectedDestination"
}
if (-not (Test-Path -LiteralPath $WebStandaloneDirectory -PathType Container)) {
    throw "Next.js standalone output was not found: $WebStandaloneDirectory"
}
if (-not (Test-Path -LiteralPath $ApiExecutable -PathType Leaf)) {
    throw "Packaged API executable was not found: $ApiExecutable"
}

$webServerSource = Join-Path $WebStandaloneDirectory "server.js"
$nestedWebDirectory = Join-Path $WebStandaloneDirectory "apps/web"
$nestedWebServerSource = Join-Path $nestedWebDirectory "server.js"
if (-not (Test-Path -LiteralPath $webServerSource -PathType Leaf) -and
    -not (Test-Path -LiteralPath $nestedWebServerSource -PathType Leaf)) {
    throw "The standalone build contains no server.js at its root or apps/web/server.js."
}

# This directory is generated exclusively for desktop packaging. Keep the removal
# narrowly scoped and validated above so user-authored application files are never touched.
if (Test-Path -LiteralPath $DestinationDirectory) {
    if ((Get-Item -LiteralPath $DestinationDirectory -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw "Refusing to replace a reparse-point resource directory: $DestinationDirectory"
    }
    Remove-Item -LiteralPath $DestinationDirectory -Recurse -Force
}
New-Item -ItemType Directory -Path $DestinationDirectory | Out-Null

$webDestination = Join-Path $DestinationDirectory "web"
$apiDestination = Join-Path $DestinationDirectory "api"
New-Item -ItemType Directory -Path $webDestination | Out-Null
New-Item -ItemType Directory -Path $apiDestination | Out-Null

Get-ChildItem -LiteralPath $WebStandaloneDirectory -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $webDestination -Recurse -Force
}

# Next may preserve monorepo-relative paths in standalone output. Flatten only the
# application subtree while retaining traced top-level node_modules dependencies.
$stagedWebServer = Join-Path $webDestination "server.js"
if (-not (Test-Path -LiteralPath $stagedWebServer -PathType Leaf)) {
    $stagedNestedDirectory = Join-Path $webDestination "apps/web"
    $stagedNestedServer = Join-Path $stagedNestedDirectory "server.js"
    if (-not (Test-Path -LiteralPath $stagedNestedServer -PathType Leaf)) {
        throw "Staged standalone output contains no launchable server.js."
    }
    Get-ChildItem -LiteralPath $stagedNestedDirectory -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $webDestination -Recurse -Force
    }
}

$nextStaticSource = Join-Path $repositoryRoot "apps/web/.next/static"
if (-not (Test-Path -LiteralPath $nextStaticSource -PathType Container)) {
    throw "Next.js static assets were not found: $nextStaticSource"
}
$nextStaticDestination = Join-Path $webDestination ".next/static"
New-Item -ItemType Directory -Path $nextStaticDestination -Force | Out-Null
Get-ChildItem -LiteralPath $nextStaticSource -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $nextStaticDestination -Recurse -Force
}

$publicSource = Join-Path $repositoryRoot "apps/web/public"
if (Test-Path -LiteralPath $publicSource -PathType Container) {
    $publicDestination = Join-Path $webDestination "public"
    New-Item -ItemType Directory -Path $publicDestination -Force | Out-Null
    Get-ChildItem -LiteralPath $publicSource -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $publicDestination -Recurse -Force
    }
}

$stagedApiExecutable = Join-Path $apiDestination "edgeos-api.exe"
Copy-Item -LiteralPath $ApiExecutable -Destination $stagedApiExecutable -Force

$apiStream = [System.IO.File]::OpenRead($stagedApiExecutable)
try {
    $hasWindowsHeader = $apiStream.ReadByte() -eq 0x4D -and $apiStream.ReadByte() -eq 0x5A
} finally {
    $apiStream.Dispose()
}
if (-not $hasWindowsHeader) {
    throw "The staged API file does not have a Windows PE header."
}

$webPackage = Get-Content -LiteralPath (Join-Path $repositoryRoot "apps/web/package.json") -Raw | ConvertFrom-Json
$apiHash = (Get-FileHash -LiteralPath $stagedApiExecutable -Algorithm SHA256).Hash.ToLowerInvariant()
$apiSignature = Get-AuthenticodeSignature -LiteralPath $stagedApiExecutable
$serverHash = (Get-FileHash -LiteralPath $stagedWebServer -Algorithm SHA256).Hash.ToLowerInvariant()
$bundleManifest = [ordered]@{
    schemaVersion = 1
    webVersion = [string]$webPackage.version
    webEntry = "web/server.js"
    webEntrySha256 = $serverHash
    apiEntry = "api/edgeos-api.exe"
    apiEntrySha256 = $apiHash
    apiSignatureStatus = [string]$apiSignature.Status
    portStrategy = "ephemeral_ipv4_loopback"
    browserApiTransport = "same_origin_allowlisted_research_bff_v2"
    realMoneyExecution = $false
}
$manifestPath = Join-Path $DestinationDirectory "desktop-bundle.json"
$bundleManifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding utf8

Write-Host "Staged desktop web server: $stagedWebServer"
Write-Host "Staged desktop API: $stagedApiExecutable"
Write-Host "Wrote bundle manifest: $manifestPath"
