# NSIS parses /D= from the raw command line, including all remaining characters.
# Unlike normal Windows arguments, its final destination must not be quoted.
function Get-EdgeosNsisInstallArguments {
    param([Parameter(Mandatory = $true)] [string]$InstallDirectory)

    if ([string]::IsNullOrWhiteSpace($InstallDirectory) -or
        $InstallDirectory -match '["\p{Cc}]') {
        throw "The NSIS installation directory must not contain quotes or control characters."
    }
    $destination = $InstallDirectory.Replace('/', '\')
    if ($destination -match '^\\\\[?.]\\' -or
        $destination -notmatch '^(?:[A-Za-z]:\\|\\\\[^\\]+\\[^\\]+\\).+') {
        throw "The NSIS installation directory must be an absolute Windows directory below a drive or share root."
    }
    if ($destination -match '(?:^|\\)\.{1,2}(?:\\|$)') {
        throw "The NSIS installation directory must not contain relative path components."
    }

    # ProcessStartInfo.Arguments passes this directly to the executable with
    # UseShellExecute=false. ArgumentList would quote a spaced /D= argument,
    # leaving a literal trailing quote in electron-builder's NSIS path parser.
    return "/S /D=$destination"
}
