[CmdletBinding()]
param(
    [string]$UnpackedDirectory,
    [int]$TimeoutSeconds = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $IsWindows) {
    throw "The packaged Electron smoke test must run on Windows."
}

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($UnpackedDirectory)) {
    $UnpackedDirectory = Join-Path $repositoryRoot "apps/desktop/release/win-unpacked"
}
$UnpackedDirectory = [System.IO.Path]::GetFullPath($UnpackedDirectory)
$desktopExecutable = Join-Path $UnpackedDirectory "EDGEOS.exe"
if (-not (Test-Path -LiteralPath $desktopExecutable -PathType Leaf)) {
    throw "Packaged desktop executable was not found: $desktopExecutable"
}

$smokeNonce = "windows-electron-$([Guid]::NewGuid().ToString('N'))"
$logPath = Join-Path $env:APPDATA "EDGEOS/logs/edgeos-desktop.log"
$pathSeparators = [char[]]@(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
)
$pathPrefix = $UnpackedDirectory.TrimEnd($pathSeparators) + [System.IO.Path]::DirectorySeparatorChar

function Get-PackagedProcesses {
    return @(
        Get-CimInstance Win32_Process |
            Where-Object {
                -not [string]::IsNullOrWhiteSpace($_.ExecutablePath) -and
                $_.ExecutablePath.StartsWith($pathPrefix, [System.StringComparison]::OrdinalIgnoreCase)
            }
    )
}

$process = $null
try {
    $process = Start-Process `
        -FilePath $desktopExecutable `
        -ArgumentList @("--edgeos-smoke-test=$smokeNonce") `
        -PassThru

    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        throw "Packaged EDGEOS.exe did not complete its integrated smoke test within $TimeoutSeconds seconds."
    }
    $process.Refresh()
    if ($process.ExitCode -ne 0) {
        if (Test-Path -LiteralPath $logPath) { Get-Content -LiteralPath $logPath -Tail 100 }
        throw "Packaged EDGEOS.exe smoke test exited with code $($process.ExitCode)."
    }

    $marker = "Packaged Electron smoke test passed ($smokeNonce)"
    if (-not (Test-Path -LiteralPath $logPath -PathType Leaf) -or
        -not (Get-Content -LiteralPath $logPath -Raw).Contains($marker)) {
        throw "Packaged EDGEOS.exe exited without recording its unique readiness marker."
    }

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(10)
    do {
        $ownedProcesses = @(Get-PackagedProcesses)
        if ($ownedProcesses.Count -eq 0) { break }
        Start-Sleep -Milliseconds 250
    } while ([DateTimeOffset]::UtcNow -lt $deadline)

    if ($ownedProcesses.Count -ne 0) {
        $details = ($ownedProcesses | ForEach-Object { "$($_.Name) pid=$($_.ProcessId)" }) -join ", "
        throw "Packaged smoke test left child processes running: $details"
    }
} finally {
    if ($null -ne $process -and -not $process.HasExited) {
        try { $process.Kill($true) } catch { }
    }
    $remainingProcesses = @(Get-PackagedProcesses)
    foreach ($remainingProcess in $remainingProcesses) {
        Stop-Process -Id $remainingProcess.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($remainingProcesses.Count -ne 0) { Start-Sleep -Milliseconds 500 }
    $remainingProcesses = @(Get-PackagedProcesses)
    if ($remainingProcesses.Count -ne 0) {
        $details = ($remainingProcesses | ForEach-Object { "$($_.Name) pid=$($_.ProcessId)" }) -join ", "
        throw "Packaged smoke-test cleanup could not stop: $details"
    }
}

Write-Host "Packaged EDGEOS.exe launched its API, standalone web server, and Electron window, then shut down cleanly."
