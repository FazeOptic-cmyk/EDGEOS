[CmdletBinding()]
param(
    [string]$ResourcesDirectory,
    [int]$StartupTimeoutSeconds = 45
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $IsWindows) {
    throw "The packaged Windows runtime smoke test must run on Windows."
}

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($ResourcesDirectory)) {
    $ResourcesDirectory = Join-Path $repositoryRoot "apps/desktop/resources"
}
$ResourcesDirectory = [System.IO.Path]::GetFullPath($ResourcesDirectory)
$apiExecutable = Join-Path $ResourcesDirectory "api/edgeos-api.exe"
$webDirectory = Join-Path $ResourcesDirectory "web"
$webServer = Join-Path $webDirectory "server.js"

foreach ($requiredFile in @($apiExecutable, $webServer)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Packaged runtime file was not found: $requiredFile"
    }
}
if (-not (Get-Command "node.exe" -ErrorAction SilentlyContinue)) {
    throw "node.exe is required to smoke-test the standalone web server."
}

$temporaryRoot = if ([string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) {
    [System.IO.Path]::GetTempPath()
} else {
    $env:RUNNER_TEMP
}
$runtimeLogDirectory = Join-Path $temporaryRoot "edgeos-runtime-smoke"
New-Item -ItemType Directory -Path $runtimeLogDirectory -Force | Out-Null

function Wait-ForHttpResponse {
    param(
        [Parameter(Mandatory = $true)] [uri]$Uri,
        [Parameter(Mandatory = $true)] [System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)] [int]$TimeoutSeconds
    )
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if ($Process.HasExited) {
            throw "Process $($Process.Id) exited before $Uri became ready (exit code $($Process.ExitCode))."
        }
        try {
            return Invoke-WebRequest -Uri $Uri -TimeoutSec 5 -MaximumRedirection 0
        } catch {
            Start-Sleep -Milliseconds 500
        }
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "Timed out after $TimeoutSeconds seconds waiting for $Uri."
}

function Stop-TestProcess {
    param([System.Diagnostics.Process]$Process)
    if ($null -ne $Process -and -not $Process.HasExited) {
        try {
            $Process.Kill($true)
            [void]$Process.WaitForExit(10000)
        } catch {
            $Process.Refresh()
            if (-not $Process.HasExited) { throw }
        }
    }
}

function Stop-ApiTestProcess {
    param(
        [System.Diagnostics.Process]$Process,
        [string]$ShutdownToken
    )
    if ($null -ne $Process -and -not $Process.HasExited) {
        try {
            Invoke-WebRequest `
                -Method Post `
                -Uri "http://127.0.0.1:8765/api/v1/desktop/shutdown" `
                -Headers @{ "x-edgeos-desktop-token" = $ShutdownToken } `
                -TimeoutSec 5 | Out-Null
            [void]$Process.WaitForExit(10000)
        } catch {
            # The process-tree fallback below handles a failed graceful stop.
        }
    }
    Stop-TestProcess -Process $Process
}

$apiProcess = $null
$apiStdout = Join-Path $runtimeLogDirectory "api.stdout.log"
$apiStderr = Join-Path $runtimeLogDirectory "api.stderr.log"
$apiRestartStdout = Join-Path $runtimeLogDirectory "api.restart.stdout.log"
$apiRestartStderr = Join-Path $runtimeLogDirectory "api.restart.stderr.log"
$priorDesktopPort = $env:EDGEOS_DESKTOP_API_PORT
$priorEdgeosEnvironment = $env:EDGEOS_ENV
$priorEdgeosMode = $env:EDGEOS_MODE
$priorCorsOrigins = $env:EDGEOS_CORS_ORIGINS
$priorShutdownToken = $env:EDGEOS_DESKTOP_SHUTDOWN_TOKEN
$priorApiToken = $env:EDGEOS_DESKTOP_API_TOKEN
$priorInstanceNonce = $env:EDGEOS_DESKTOP_INSTANCE_NONCE
$priorLocalDataRoot = $env:EDGEOS_LOCAL_DATA_ROOT
$priorLocalDataPath = $env:EDGEOS_LOCAL_DATA_PATH
$instanceNonce = "windows-runtime-smoke-$([Guid]::NewGuid().ToString('N'))"
$shutdownToken = "windows-runtime-stop-$([Guid]::NewGuid().ToString('N'))"
$apiToken = "windows-runtime-api-$([Guid]::NewGuid().ToString('N'))"
$localDataRoot = Join-Path $runtimeLogDirectory "local-$([Guid]::NewGuid().ToString('N'))"
$localDataPath = Join-Path $localDataRoot "data/edgeos-local.sqlite3"
New-Item -ItemType Directory -Path (Split-Path -Parent $localDataPath) -Force | Out-Null
try {
    $env:EDGEOS_DESKTOP_API_PORT = "8765"
    $env:EDGEOS_ENV = "desktop"
    $env:EDGEOS_MODE = "demo"
    $env:EDGEOS_CORS_ORIGINS = "http://127.0.0.1:3210"
    $env:EDGEOS_DESKTOP_INSTANCE_NONCE = $instanceNonce
    $env:EDGEOS_DESKTOP_SHUTDOWN_TOKEN = $shutdownToken
    $env:EDGEOS_DESKTOP_API_TOKEN = $apiToken
    $env:EDGEOS_LOCAL_DATA_ROOT = $localDataRoot
    $env:EDGEOS_LOCAL_DATA_PATH = $localDataPath
    $apiProcess = Start-Process `
        -FilePath $apiExecutable `
        -ArgumentList @("--port", "8765", "--log-level", "warning") `
        -PassThru `
        -WindowStyle Hidden `
        -RedirectStandardOutput $apiStdout `
        -RedirectStandardError $apiStderr
    $apiResponse = Wait-ForHttpResponse `
        -Uri "http://127.0.0.1:8765/api/v1/desktop/readiness" `
        -Process $apiProcess `
        -TimeoutSeconds $StartupTimeoutSeconds
    if ($apiResponse.StatusCode -ne 200) {
        throw "Packaged API health returned HTTP $($apiResponse.StatusCode)."
    }
    $apiHealth = $apiResponse.Content | ConvertFrom-Json
    if ($apiHealth.status -ne "ok" -or $apiHealth.service -ne "edgeos-api" -or $apiHealth.instance_nonce -ne $instanceNonce) {
        throw "Packaged API readiness did not return the exact per-launch identity proof."
    }
    $unauthenticatedApiResponse = Invoke-WebRequest `
        -Uri "http://127.0.0.1:8765/api/v1/health" `
        -SkipHttpErrorCheck `
        -TimeoutSec 5
    if ($unauthenticatedApiResponse.StatusCode -ne 403) {
        throw "Packaged API did not require its per-launch business-route token."
    }
    $corsResponse = Invoke-WebRequest `
        -Uri "http://127.0.0.1:8765/api/v1/health" `
        -Headers @{ Origin = "http://127.0.0.1:3210"; "x-edgeos-desktop-token" = $apiToken } `
        -TimeoutSec 5
    if ($corsResponse.Headers["Access-Control-Allow-Origin"] -ne "http://127.0.0.1:3210") {
        throw "Packaged API did not authorize the fixed desktop web origin."
    }
    $hostResponse = Invoke-WebRequest `
        -Uri "http://127.0.0.1:8765/api/v1/health" `
        -Headers @{ Host = "attacker.example"; "x-edgeos-desktop-token" = $apiToken } `
        -SkipHttpErrorCheck `
        -TimeoutSec 5
    if ($hostResponse.StatusCode -ne 400) {
        throw "Packaged API did not reject a hostile Host header."
    }
    $persistedAllocation = 4321
    $savedSettingsResponse = Invoke-WebRequest `
        -Method Put `
        -Uri "http://127.0.0.1:8765/api/v1/settings" `
        -Headers @{ "x-edgeos-desktop-token" = $apiToken } `
        -ContentType "application/json" `
        -Body (@{ default_allocation = $persistedAllocation } | ConvertTo-Json -Compress) `
        -TimeoutSec 5
    $savedSettings = $savedSettingsResponse.Content | ConvertFrom-Json
    if ([int]$savedSettings.default_allocation -ne $persistedAllocation) {
        throw "Packaged API did not save the local setting before restart."
    }

    Stop-ApiTestProcess -Process $apiProcess -ShutdownToken $shutdownToken
    $apiProcess = $null
    $apiProcess = Start-Process `
        -FilePath $apiExecutable `
        -ArgumentList @("--port", "8765", "--log-level", "warning") `
        -PassThru `
        -WindowStyle Hidden `
        -RedirectStandardOutput $apiRestartStdout `
        -RedirectStandardError $apiRestartStderr
    [void](Wait-ForHttpResponse `
        -Uri "http://127.0.0.1:8765/api/v1/desktop/readiness" `
        -Process $apiProcess `
        -TimeoutSeconds $StartupTimeoutSeconds)
    $restoredSettingsResponse = Invoke-WebRequest `
        -Uri "http://127.0.0.1:8765/api/v1/settings" `
        -Headers @{ "x-edgeos-desktop-token" = $apiToken } `
        -TimeoutSec 5
    $restoredSettings = $restoredSettingsResponse.Content | ConvertFrom-Json
    if ([int]$restoredSettings.default_allocation -ne $persistedAllocation) {
        throw "Packaged API did not restore the local setting after process restart."
    }
    Write-Host "Packaged API smoke test passed on 127.0.0.1:8765."
} catch {
    if (Test-Path -LiteralPath $apiStderr) { Get-Content -LiteralPath $apiStderr -Tail 100 }
    if (Test-Path -LiteralPath $apiRestartStderr) { Get-Content -LiteralPath $apiRestartStderr -Tail 100 }
    Stop-ApiTestProcess -Process $apiProcess -ShutdownToken $shutdownToken
    throw
} finally {
    if ($null -eq $priorDesktopPort) { Remove-Item Env:EDGEOS_DESKTOP_API_PORT -ErrorAction SilentlyContinue } else { $env:EDGEOS_DESKTOP_API_PORT = $priorDesktopPort }
    if ($null -eq $priorEdgeosEnvironment) { Remove-Item Env:EDGEOS_ENV -ErrorAction SilentlyContinue } else { $env:EDGEOS_ENV = $priorEdgeosEnvironment }
    if ($null -eq $priorEdgeosMode) { Remove-Item Env:EDGEOS_MODE -ErrorAction SilentlyContinue } else { $env:EDGEOS_MODE = $priorEdgeosMode }
    if ($null -eq $priorCorsOrigins) { Remove-Item Env:EDGEOS_CORS_ORIGINS -ErrorAction SilentlyContinue } else { $env:EDGEOS_CORS_ORIGINS = $priorCorsOrigins }
    if ($null -eq $priorShutdownToken) { Remove-Item Env:EDGEOS_DESKTOP_SHUTDOWN_TOKEN -ErrorAction SilentlyContinue } else { $env:EDGEOS_DESKTOP_SHUTDOWN_TOKEN = $priorShutdownToken }
    if ($null -eq $priorApiToken) { Remove-Item Env:EDGEOS_DESKTOP_API_TOKEN -ErrorAction SilentlyContinue } else { $env:EDGEOS_DESKTOP_API_TOKEN = $priorApiToken }
    if ($null -eq $priorInstanceNonce) { Remove-Item Env:EDGEOS_DESKTOP_INSTANCE_NONCE -ErrorAction SilentlyContinue } else { $env:EDGEOS_DESKTOP_INSTANCE_NONCE = $priorInstanceNonce }
    if ($null -eq $priorLocalDataRoot) { Remove-Item Env:EDGEOS_LOCAL_DATA_ROOT -ErrorAction SilentlyContinue } else { $env:EDGEOS_LOCAL_DATA_ROOT = $priorLocalDataRoot }
    if ($null -eq $priorLocalDataPath) { Remove-Item Env:EDGEOS_LOCAL_DATA_PATH -ErrorAction SilentlyContinue } else { $env:EDGEOS_LOCAL_DATA_PATH = $priorLocalDataPath }
}

$priorPort = $env:PORT
$priorHostname = $env:HOSTNAME
$priorNodeEnvironment = $env:NODE_ENV
$priorEdgeosWebEnvironment = $env:EDGEOS_ENV
$priorInternalApiOrigin = $env:EDGEOS_INTERNAL_API_ORIGIN
$priorDesktopWebOrigin = $env:EDGEOS_DESKTOP_WEB_ORIGIN
$priorWebInstanceNonce = $env:EDGEOS_DESKTOP_INSTANCE_NONCE
$priorWebApiToken = $env:EDGEOS_DESKTOP_API_TOKEN
$webProcess = $null
$webStdout = Join-Path $runtimeLogDirectory "web.stdout.log"
$webStderr = Join-Path $runtimeLogDirectory "web.stderr.log"
try {
    $env:PORT = "3210"
    $env:HOSTNAME = "127.0.0.1"
    $env:NODE_ENV = "production"
    $env:EDGEOS_ENV = "desktop"
    $env:EDGEOS_DESKTOP_WEB_ORIGIN = "http://127.0.0.1:3210"
    $env:EDGEOS_INTERNAL_API_ORIGIN = "http://127.0.0.1:8765"
    $env:EDGEOS_DESKTOP_INSTANCE_NONCE = $instanceNonce
    $env:EDGEOS_DESKTOP_API_TOKEN = $apiToken
    $webProcess = Start-Process `
        -FilePath (Get-Command "node.exe").Source `
        -ArgumentList @("server.js") `
        -WorkingDirectory $webDirectory `
        -PassThru `
        -WindowStyle Hidden `
        -RedirectStandardOutput $webStdout `
        -RedirectStandardError $webStderr
    $webResponse = Wait-ForHttpResponse `
        -Uri "http://127.0.0.1:3210/" `
        -Process $webProcess `
        -TimeoutSeconds $StartupTimeoutSeconds
    if ($webResponse.StatusCode -ne 200) {
        throw "Standalone web server returned HTTP $($webResponse.StatusCode)."
    }
    $webHealthResponse = Invoke-WebRequest `
        -Uri "http://127.0.0.1:3210/api/desktop-health" `
        -TimeoutSec 5
    $webHealth = $webHealthResponse.Content | ConvertFrom-Json
    if ($webHealth.status -ne "ok" -or $webHealth.service -ne "edgeos-web" -or $webHealth.instance_nonce -ne $instanceNonce) {
        throw "Standalone web readiness did not return the exact per-launch identity proof."
    }
    $hostileWebHealth = Invoke-WebRequest `
        -Uri "http://127.0.0.1:3210/api/desktop-health" `
        -Headers @{ Host = "attacker.example" } `
        -SkipHttpErrorCheck `
        -TimeoutSec 5
    if ($hostileWebHealth.StatusCode -ne 403) {
        throw "Standalone web readiness did not reject a hostile Host header."
    }
    $proxyResponse = Invoke-WebRequest `
        -Uri "http://127.0.0.1:3210/api/edgeos/api/v1/health" `
        -TimeoutSec 5
    $proxyHealth = $proxyResponse.Content | ConvertFrom-Json
    if ($proxyResponse.StatusCode -ne 200 -or $proxyHealth.service -ne "edgeos-api" -or $proxyHealth.is_demo -ne $true) {
        throw "The packaged same-origin API proxy failed its demo health check."
    }
    if ($proxyResponse.Headers["X-EDGEOS-Mode"] -ne "demo" -or
        $proxyResponse.Headers["X-EDGEOS-Mode-Contract"] -ne "1") {
        throw "The packaged API proxy did not preserve the versioned demo-mode handshake."
    }
    # An invalid calculation payload must reach the explicitly allowlisted API
    # operation and be rejected by FastAPI validation, not by the BFF. This
    # proves research mutations work without exposing an open proxy.
    $mutationResponse = Invoke-WebRequest `
        -Method Post `
        -Uri "http://127.0.0.1:3210/api/edgeos/api/v1/simulate" `
        -Headers @{ Origin = "http://127.0.0.1:3210" } `
        -ContentType "application/json" `
        -Body "{}" `
        -SkipHttpErrorCheck `
        -TimeoutSec 5
    if ($mutationResponse.StatusCode -ne 422 -or
        $mutationResponse.Headers["X-EDGEOS-Mode"] -ne "demo") {
        throw "The packaged allowlisted research mutation path did not reach the demo API safely."
    }
    $unlistedProxyResponse = Invoke-WebRequest `
        -Uri "http://127.0.0.1:3210/api/edgeos/api/v1/internal/secrets" `
        -SkipHttpErrorCheck `
        -TimeoutSec 5
    if ($unlistedProxyResponse.StatusCode -ne 404) {
        throw "The packaged API gateway exposed a route outside its explicit allowlist."
    }
    $crossOriginProxy = Invoke-WebRequest `
        -Uri "http://127.0.0.1:3210/api/edgeos/api/v1/health" `
        -Headers @{ Origin = "https://attacker.example"; "Sec-Fetch-Site" = "cross-site" } `
        -SkipHttpErrorCheck `
        -TimeoutSec 5
    if ($crossOriginProxy.StatusCode -ne 403) {
        throw "The packaged API proxy did not reject a cross-origin browser request."
    }
    Write-Host "Standalone Next.js smoke test passed on 127.0.0.1:3210."
} catch {
    if (Test-Path -LiteralPath $webStderr) { Get-Content -LiteralPath $webStderr -Tail 100 }
    throw
} finally {
    Stop-TestProcess -Process $webProcess
    Stop-ApiTestProcess -Process $apiProcess -ShutdownToken $shutdownToken
    if ($null -eq $priorPort) { Remove-Item Env:PORT -ErrorAction SilentlyContinue } else { $env:PORT = $priorPort }
    if ($null -eq $priorHostname) { Remove-Item Env:HOSTNAME -ErrorAction SilentlyContinue } else { $env:HOSTNAME = $priorHostname }
    if ($null -eq $priorNodeEnvironment) { Remove-Item Env:NODE_ENV -ErrorAction SilentlyContinue } else { $env:NODE_ENV = $priorNodeEnvironment }
    if ($null -eq $priorEdgeosWebEnvironment) { Remove-Item Env:EDGEOS_ENV -ErrorAction SilentlyContinue } else { $env:EDGEOS_ENV = $priorEdgeosWebEnvironment }
    if ($null -eq $priorInternalApiOrigin) { Remove-Item Env:EDGEOS_INTERNAL_API_ORIGIN -ErrorAction SilentlyContinue } else { $env:EDGEOS_INTERNAL_API_ORIGIN = $priorInternalApiOrigin }
    if ($null -eq $priorDesktopWebOrigin) { Remove-Item Env:EDGEOS_DESKTOP_WEB_ORIGIN -ErrorAction SilentlyContinue } else { $env:EDGEOS_DESKTOP_WEB_ORIGIN = $priorDesktopWebOrigin }
    if ($null -eq $priorWebInstanceNonce) { Remove-Item Env:EDGEOS_DESKTOP_INSTANCE_NONCE -ErrorAction SilentlyContinue } else { $env:EDGEOS_DESKTOP_INSTANCE_NONCE = $priorWebInstanceNonce }
    if ($null -eq $priorWebApiToken) { Remove-Item Env:EDGEOS_DESKTOP_API_TOKEN -ErrorAction SilentlyContinue } else { $env:EDGEOS_DESKTOP_API_TOKEN = $priorWebApiToken }
}
