#requires -Version 7.0
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "windows-build-storage.ps1")

$storageTestCount = 0
function Assert-StorageTest {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Invoke-StorageTest {
    param([string]$Name, [scriptblock]$Test)
    try {
        & $Test
        $script:storageTestCount++
    } catch {
        throw "Build storage test '$Name' failed: $($_.Exception.Message)"
    }
}

function Assert-StorageRejects {
    param([scriptblock]$Action)
    $rejected = $false
    try { & $Action | Out-Null } catch { $rejected = $true }
    Assert-StorageTest $rejected "Expected the storage helper to reject this input."
}

function Assert-StorageEnvironmentUnchanged {
    param([object[]]$Snapshot)
    foreach ($entry in $Snapshot) {
        $actual = [Environment]::GetEnvironmentVariable($entry.Name, $entry.Scope)
        Assert-StorageTest ($actual -ceq $entry.Value) "Changed $($entry.Scope) environment variable $($entry.Name)."
    }
}

$storageTestNames = @(
    "npm_config_cache", "electron_config_cache", "ELECTRON_BUILDER_CACHE",
    "NEXT_SWC_PATH", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "UV_PYTHON_BIN_DIR",
    "PYINSTALLER_CONFIG_DIR", "TEMP", "TMP", "RUNNER_TEMP"
)
$storageProfileNames = @("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA")
$storageOriginalEnvironment = @(
    foreach ($name in $storageTestNames) {
        [pscustomobject]@{
            Name = $name
            Scope = [EnvironmentVariableTarget]::Process
            Value = [Environment]::GetEnvironmentVariable($name, "Process")
        }
    }
)
$storageProtectedEnvironment = @(
    foreach ($name in $storageProfileNames) {
        [pscustomobject]@{
            Name = $name
            Scope = [EnvironmentVariableTarget]::Process
            Value = [Environment]::GetEnvironmentVariable($name, "Process")
        }
    }
    foreach ($scope in @([EnvironmentVariableTarget]::User, [EnvironmentVariableTarget]::Machine)) {
        foreach ($name in @($storageTestNames) + @($storageProfileNames)) {
            [pscustomobject]@{
                Name = $name
                Scope = $scope
                Value = [Environment]::GetEnvironmentVariable($name, $scope)
            }
        }
    }
)

$storageTemporaryBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$storageFixtureName = "edgeos-storage-test-$([Guid]::NewGuid().ToString('N'))"
$storageFixtureRoot = [IO.Path]::GetFullPath((Join-Path $storageTemporaryBase $storageFixtureName))
$storageFixtureCreated = $false
$storageRepository = Join-Path $storageFixtureRoot "source with spaces"
$storageCreatedLinks = [Collections.Generic.List[string]]::new()
try {
    if (Test-Path -LiteralPath $storageFixtureRoot) {
        throw "Refusing to reuse an existing test directory: $storageFixtureRoot"
    }
    New-Item -ItemType Directory -Path $storageFixtureRoot | Out-Null
    $storageFixtureCreated = $true
    New-Item -ItemType Directory -Path $storageRepository | Out-Null
    $storageExpectedPaths = Get-EdgeosBuildStorageEnvironment -RepositoryRoot $storageRepository

    Invoke-StorageTest "absolute mapping and exact environment names" {
        Assert-StorageTest ($storageExpectedPaths -is [Collections.IDictionary]) "Expected an environment dictionary."
        Assert-StorageTest ($storageExpectedPaths.Count -eq $storageTestNames.Count) "Unexpected environment mapping count."
        Assert-StorageTest ((@($storageExpectedPaths.Keys) -join "|") -ceq ($storageTestNames -join "|")) "Environment names or their order changed."
        $cachePrefix = (Join-Path $storageRepository ".edgeos-build-cache") + [IO.Path]::DirectorySeparatorChar
        foreach ($name in $storageTestNames) {
            $mappedPath = [string]$storageExpectedPaths[$name]
            Assert-StorageTest ([IO.Path]::IsPathFullyQualified($mappedPath)) "Expected an absolute path for $name."
            Assert-StorageTest ([IO.Path]::GetFullPath($mappedPath).StartsWith($cachePrefix, [StringComparison]::Ordinal)) "The $name path escaped the repository cache."
        }
        Assert-StorageTest (-not (Test-Path -LiteralPath (Join-Path $storageRepository ".edgeos-build-cache"))) "Reading the mapping created directories."
    }

    Invoke-StorageTest "relative repository rejected" {
        Assert-StorageRejects { Get-EdgeosBuildStorageEnvironment -RepositoryRoot "relative source" }
    }
    Invoke-StorageTest "drive root rejected" {
        Assert-StorageRejects { Get-EdgeosBuildStorageEnvironment -RepositoryRoot ([IO.Path]::GetPathRoot($storageRepository)) }
    }

    # Test both absent and existing values for every setting across the two runs.
    foreach ($failAction in @($false, $true)) {
        $storagePriorValues = @()
        for ($index = 0; $index -lt $storageTestNames.Count; $index++) {
            $name = $storageTestNames[$index]
            $value = if (($index % 2 -eq 0) -xor $failAction) { "prior value with spaces $index" } else { $null }
            if ($null -eq $value) {
                [Environment]::SetEnvironmentVariable($name, [NullString]::Value, "Process")
            } else {
                [Environment]::SetEnvironmentVariable($name, $value, "Process")
            }
            $storagePriorValues += [pscustomobject]@{
                Name = $name
                Scope = [EnvironmentVariableTarget]::Process
                Value = $value
            }
        }
        $storageActionProbe = [pscustomobject]@{ Ran = $false }
        $storageFailureMarker = "intentional storage action failure"
        Invoke-StorageTest "environment and directories during action (throws=$failAction)" {
            $caughtMessage = $null
            try {
                Invoke-WithEdgeosBuildStorage -RepositoryRoot $storageRepository -Action {
                    $storageActionProbe.Ran = $true
                    foreach ($name in $storageTestNames) {
                        $actual = [Environment]::GetEnvironmentVariable($name, "Process")
                        Assert-StorageTest ($actual -ceq $storageExpectedPaths[$name]) "Incorrect active value for $name."
                        Assert-StorageTest (Test-Path -LiteralPath $actual -PathType Container) "Missing active directory for $name."
                    }
                    Assert-StorageEnvironmentUnchanged $storageProtectedEnvironment
                    if ($failAction) { throw $storageFailureMarker }
                }
            } catch {
                $caughtMessage = $_.Exception.Message
            }
            Assert-StorageTest $storageActionProbe.Ran "The storage helper did not invoke its action."
            if ($failAction) {
                Assert-StorageTest ($caughtMessage -ceq $storageFailureMarker) "The expected action failure was not propagated: $caughtMessage"
            } else {
                Assert-StorageTest ($null -eq $caughtMessage) "The action failed: $caughtMessage"
            }
        }
        Invoke-StorageTest "environment restored after action (throws=$failAction)" {
            Assert-StorageEnvironmentUnchanged $storagePriorValues
            Assert-StorageEnvironmentUnchanged $storageProtectedEnvironment
        }
    }

    Invoke-StorageTest "empty environment value restored" {
        $emptyName = $storageTestNames[0]
        $priorEmptyValue = [Environment]::GetEnvironmentVariable($emptyName, "Process")
        try {
            [Environment]::SetEnvironmentVariable($emptyName, "", "Process")
            # Older .NET versions treat empty values as absent; preserve the
            # representation supported by the current runtime in either case.
            $emptySnapshot = @([pscustomobject]@{
                Name = $emptyName
                Scope = [EnvironmentVariableTarget]::Process
                Value = [Environment]::GetEnvironmentVariable($emptyName, "Process")
            })
            Invoke-WithEdgeosBuildStorage -RepositoryRoot $storageRepository -Action { }
            Assert-StorageEnvironmentUnchanged $emptySnapshot
            Assert-StorageEnvironmentUnchanged $storageProtectedEnvironment
        } finally {
            $restoreValue = if ($null -eq $priorEmptyValue) { [NullString]::Value } else { $priorEmptyValue }
            [Environment]::SetEnvironmentVariable($emptyName, $restoreValue, "Process")
        }
    }

    foreach ($linkLocation in @("cache root", "mapped directory")) {
        Invoke-StorageTest "reject $linkLocation redirect" {
            $linkRepository = Join-Path $storageFixtureRoot "$linkLocation source"
            $linkTarget = Join-Path $storageFixtureRoot "$linkLocation target"
            New-Item -ItemType Directory -Path $linkRepository, $linkTarget | Out-Null
            $linkMapping = Get-EdgeosBuildStorageEnvironment -RepositoryRoot $linkRepository
            $linkPath = if ($linkLocation -eq "cache root") {
                Join-Path $linkRepository ".edgeos-build-cache"
            } else {
                $linkMapping["npm_config_cache"]
            }
            New-Item -ItemType Directory -Path (Split-Path -Parent $linkPath) -Force | Out-Null
            $linkType = if ($IsWindows) { "Junction" } else { "SymbolicLink" }
            New-Item -ItemType $linkType -Path $linkPath -Target $linkTarget | Out-Null
            $storageCreatedLinks.Add($linkPath)
            Assert-StorageTest ([bool]((Get-Item -LiteralPath $linkPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) "The redirect fixture is not a link."
            $storageLinkProbe = [pscustomobject]@{ Ran = $false }
            Assert-StorageRejects {
                Invoke-WithEdgeosBuildStorage -RepositoryRoot $linkRepository -Action { $storageLinkProbe.Ran = $true }
            }
            Assert-StorageTest (-not $storageLinkProbe.Ran) "The action ran with redirected storage."
            Assert-StorageTest (@(Get-ChildItem -LiteralPath $linkTarget -Force).Count -eq 0) "The helper wrote through a redirected directory."
            Assert-StorageEnvironmentUnchanged $storagePriorValues
            Assert-StorageEnvironmentUnchanged $storageProtectedEnvironment
        }
    }
} finally {
    foreach ($entry in $storageOriginalEnvironment) {
        if ($null -eq $entry.Value) {
            [Environment]::SetEnvironmentVariable($entry.Name, [NullString]::Value, "Process")
        } else {
            [Environment]::SetEnvironmentVariable($entry.Name, $entry.Value, "Process")
        }
    }
    if ($storageFixtureCreated -and (Test-Path -LiteralPath $storageFixtureRoot)) {
        $expectedRoot = [IO.Path]::GetFullPath((Join-Path $storageTemporaryBase $storageFixtureName))
        $fixtureItem = Get-Item -LiteralPath $storageFixtureRoot -Force
        if ($storageFixtureRoot -cne $expectedRoot -or
            $fixtureItem.Name -cne $storageFixtureName -or
            ($fixtureItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing to clean an unexpected test directory: $storageFixtureRoot"
        }
        # Remove only the link itself before recursive fixture cleanup.
        foreach ($linkPath in $storageCreatedLinks) {
            if (Test-Path -LiteralPath $linkPath) { [IO.Directory]::Delete($linkPath) }
        }
        Remove-Item -LiteralPath $storageFixtureRoot -Recurse -Force
    }
}

Write-Host "Windows build storage tests passed ($storageTestCount cases)."
