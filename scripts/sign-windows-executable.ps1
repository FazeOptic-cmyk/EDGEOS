[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Executable,
    [Parameter(Mandatory = $true)]
    [string]$CertificatePath,
    [Parameter(Mandatory = $true)]
    [string]$CertificatePassword,
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $IsWindows) {
    throw "Authenticode signing must run on Windows."
}
$Executable = [System.IO.Path]::GetFullPath($Executable)
$CertificatePath = [System.IO.Path]::GetFullPath($CertificatePath)
if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "Executable to sign was not found: $Executable"
}
if (-not (Test-Path -LiteralPath $CertificatePath -PathType Leaf)) {
    throw "Signing certificate was not found: $CertificatePath"
}

$signToolCommand = Get-Command "signtool.exe" -ErrorAction SilentlyContinue
if ($null -ne $signToolCommand) {
    $signTool = $signToolCommand.Source
} else {
    $windowsKitsRoot = Join-Path ${env:ProgramFiles(x86)} "Windows Kits/10/bin"
    $signToolCandidates = @(
        Get-ChildItem -Path (Join-Path $windowsKitsRoot "*/x64/signtool.exe") -File -ErrorAction SilentlyContinue |
            Sort-Object { [version]$_.Directory.Parent.Name } -Descending
    )
    if ($signToolCandidates.Count -eq 0) {
        throw "signtool.exe was not found on PATH or in the Windows 10 SDK."
    }
    $signTool = $signToolCandidates[0].FullName
}

# Import into the current-user certificate store so the PFX password is not
# exposed in signtool's process command line. Do not request PersistKeySet: the
# temporary current-user-store entry is removed and the loaded certificate is
# disposed in the finally block. Run release signing only on an isolated runner.
$certificate = $null
$certificateAdded = $false
$store = [System.Security.Cryptography.X509Certificates.X509Store]::new(
    [System.Security.Cryptography.X509Certificates.StoreName]::My,
    [System.Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser
)
try {
    $flags = [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::UserKeySet
    $certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
        $CertificatePath,
        $CertificatePassword,
        $flags
    )
    if (-not $certificate.HasPrivateKey) {
        throw "The supplied PFX does not contain an accessible private key."
    }

    $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
    $existing = $store.Certificates.Find(
        [System.Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint,
        $certificate.Thumbprint,
        $false
    )
    if ($existing.Count -ne 0) {
        throw "The signing certificate already exists in CurrentUser\\My; use an isolated release runner."
    }
    $store.Add($certificate)
    $certificateAdded = $true
    $store.Close()

    & $signTool sign /fd SHA256 /td SHA256 /tr $TimestampUrl /sha1 $certificate.Thumbprint /s My $Executable
    if ($LASTEXITCODE -ne 0) {
        throw "signtool failed with exit code $LASTEXITCODE."
    }

    & $signTool verify /pa /all /tw $Executable
    if ($LASTEXITCODE -ne 0) {
        throw "signtool verification failed with exit code $LASTEXITCODE."
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $Executable
    if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
        throw "Signed executable has Authenticode status '$($signature.Status)'."
    }
    if ($signature.SignerCertificate.Thumbprint -ne $certificate.Thumbprint) {
        throw "Signed executable does not use the certificate supplied for this build."
    }
    if ($null -eq $signature.TimeStamperCertificate) {
        throw "Signed executable has no verified RFC 3161 timestamp."
    }
    Write-Host "Authenticode signature and timestamp verified: $Executable"
} finally {
    try {
        if ($certificateAdded -and $null -ne $certificate) {
            # X509Store does not expose a portable public IsOpen contract across
            # every supported PowerShell/.NET combination. Close is idempotent;
            # reopen explicitly for the narrowly scoped cleanup operation.
            $store.Close()
            $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
            $store.Remove($certificate)
        }
    } finally {
        $store.Close()
        if ($null -ne $certificate) { $certificate.Dispose() }
    }
}
