# Shared by the guided Windows launcher and dependency-free regression tests.
# Do not change user/machine environment settings or redirect normal app data.
function Get-EdgeosBuildStorageEnvironment {
    param([Parameter(Mandatory = $true)] [string]$RepositoryRoot)

    if (-not [IO.Path]::IsPathFullyQualified($RepositoryRoot)) {
        throw "The build source folder must be an absolute path."
    }
    $root = [IO.Path]::GetFullPath($RepositoryRoot)
    if ($root.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) -eq
        [IO.Path]::GetPathRoot($root).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)) {
        throw "Extract EDGEOS into its own folder, not the root of a drive."
    }
    $cacheRoot = Join-Path $root ".edgeos-build-cache"
    return [ordered]@{
        npm_config_cache = Join-Path $cacheRoot "npm"
        # Electron's npm install script reads this lowercase name, NOT ELECTRON_CACHE.
        electron_config_cache = Join-Path $cacheRoot "electron"
        ELECTRON_BUILDER_CACHE = Join-Path $cacheRoot "electron-builder"
        NEXT_SWC_PATH = Join-Path $cacheRoot "next-swc"
        UV_CACHE_DIR = Join-Path $cacheRoot "uv"
        UV_PYTHON_INSTALL_DIR = Join-Path $cacheRoot "python"
        UV_PYTHON_BIN_DIR = Join-Path $cacheRoot "python-bin"
        PYINSTALLER_CONFIG_DIR = Join-Path $cacheRoot "pyinstaller"
        TEMP = Join-Path $cacheRoot "temp"
        TMP = Join-Path $cacheRoot "temp"
        RUNNER_TEMP = Join-Path $cacheRoot "temp"
    }
}

function Invoke-WithEdgeosBuildStorage {
    param(
        [Parameter(Mandatory = $true)] [string]$RepositoryRoot,
        [Parameter(Mandatory = $true)] [scriptblock]$Action
    )

    $settings = Get-EdgeosBuildStorageEnvironment -RepositoryRoot $RepositoryRoot
    $cacheRoot = Join-Path ([IO.Path]::GetFullPath($RepositoryRoot)) ".edgeos-build-cache"
    # Check the cache root before its children; never follow a redirected cache
    # into a different drive or another user's directory.
    $directories = @($cacheRoot) + @($settings.Values | Select-Object -Unique)
    foreach ($directory in $directories) {
        if (Test-Path -LiteralPath $directory) {
            $item = Get-Item -LiteralPath $directory -Force
            if (-not $item.PSIsContainer -or
                ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw "Build storage must be a regular directory, not a file or link: $directory"
            }
        } else {
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
        }
    }

    $saved = @{}
    try {
        foreach ($name in $settings.Keys) {
            $saved[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
            [Environment]::SetEnvironmentVariable($name, $settings[$name], "Process")
        }
        & $Action
    } finally {
        foreach ($name in $saved.Keys) {
            # PowerShell's .NET binder can coerce $null to an empty string.
            # Newer runtimes preserve empty env values, so explicitly restore
            # absence instead of leaving a blank cache/TEMP override behind.
            $priorValue = if ($null -eq $saved[$name]) { [NullString]::Value } else { $saved[$name] }
            [Environment]::SetEnvironmentVariable($name, $priorValue, "Process")
        }
    }
}
