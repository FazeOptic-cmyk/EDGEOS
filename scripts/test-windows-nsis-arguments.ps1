#requires -Version 7.0
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "windows-nsis-arguments.ps1")

$nsisTestCount = 0
$validDestinations = @(
    @{ Input = 'D:\EDGEOS\cache\application'; Expected = 'D:\EDGEOS\cache\application' },
    @{ Input = 'D:\EDGEOS build\.edgeos-build-cache\temp\application'; Expected = 'D:\EDGEOS build\.edgeos-build-cache\temp\application' },
    @{ Input = 'C:\Users\Test User\AppData\Local\Temp\application'; Expected = 'C:\Users\Test User\AppData\Local\Temp\application' },
    @{ Input = '\\build-server\release share\EDGEOS build\application'; Expected = '\\build-server\release share\EDGEOS build\application' },
    @{ Input = 'D:/EDGEOS build/application'; Expected = 'D:\EDGEOS build\application' }
)
foreach ($case in $validDestinations) {
    $arguments = Get-EdgeosNsisInstallArguments -InstallDirectory $case.Input
    if ($arguments -cne "/S /D=$($case.Expected)") {
        throw "NSIS arguments did not preserve the expected destination: $($case.Input)"
    }
    # electron-builder's GetDParameter macro takes everything after the /D=.
    # Assert that parsing contract, including spaces, against the raw arguments.
    $parsedDestination = $arguments.Substring($arguments.IndexOf('/D=', [StringComparison]::Ordinal) + 3)
    if ($parsedDestination -cne $case.Expected -or $parsedDestination.Contains('"')) {
        throw "The NSIS raw command-line parser would receive a different installation directory."
    }
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.UseShellExecute = $false
    $startInfo.Arguments = $arguments
    if ($startInfo.ArgumentList.Count -ne 0 -or $startInfo.Arguments -cne $arguments) {
        throw "The NSIS command line must use raw Arguments without ArgumentList quoting."
    }
    $nsisTestCount++
}

$invalidDestinations = @(
    'relative\application', 'D:application', '\application', '/tmp/application',
    'D:\', '\\server', '\\server\share', '\\server\share\',
    '\\?\D:\application', '\\.\D:\application',
    'D:\build\..\application', 'D:\build\.\application',
    'D:\build" folder\application', "D:\build`nfolder\application",
    "D:\build`rfolder\application", "D:\build`tfolder\application",
    ("D:\build" + [char]0 + '\application'),
    ("D:\build" + [char]127 + '\application')
)
foreach ($destination in $invalidDestinations) {
    $rejected = $false
    try { Get-EdgeosNsisInstallArguments -InstallDirectory $destination | Out-Null } catch { $rejected = $true }
    if (-not $rejected) { throw "NSIS accepted a relative, root, quoted, or control-character destination." }
    $nsisTestCount++
}

Write-Host "Windows NSIS argument tests passed ($nsisTestCount cases)."
