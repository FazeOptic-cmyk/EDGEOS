# EDGEOS Windows installer

EDGEOS can be packaged as an assisted, per-user NSIS installer for x64 Windows.
The checked-in pipeline bundles the Next.js interface, a loopback-only FastAPI
executable, and an Electron desktop host. It does not install a system service,
place orders, or enable continuous ingestion.

> **Artifact status (2026-10-20):** this repository audit ran on Linux and did
> not build, sign, install, or publish an `.exe`. Obtain a Windows CI artifact or
> build on Windows; do not infer that a distributable installer accompanies the
> source tree.

The standard build is explicitly a **demo build**. Its preloaded market and
analytics fixtures are deterministic and none of them are live prices or real
account performance. Device-local paper positions, fills, settlements, ledger
entries, watchlists, alert-rule configuration, settings, and saved opportunities
are stored in SQLite and survive an ordinary close, restart, upgrade, or
reinstall. They remain simulated, do not sync to another device, and do not make
the bundled market fixture live.
The advised real-money capital for this release is **$0**: it is research and
paper-trading software, not an execution system or a basis for unattended bets.

## Install a published artifact

The Windows GitHub Actions workflow labels artifacts by trust level:

```text
edgeos-windows-installer-SIGNED-<workflow-run-number>-<run-attempt>
edgeos-windows-installer-UNSIGNED-DEV-<workflow-run-number>-<run-attempt>
```

Extract it before running the installer. The extracted directory contains:

```text
EDGEOS Setup <version>.exe
windows-build-manifest.json
SHA256SUMS.txt
```

The packager may also emit a blockmap or channel metadata; the verifier copies
those files when present. They are not required installation inputs. Automatic
updates are not implemented in this release, and no update endpoint is
configured.

For a distributable release, do not treat signature verification as optional.
The manifest must report a signed installer, packaged `EDGEOS.exe`, and bundled
`edgeos-api.exe`, and each Authenticode signature must be valid, timestamped,
and issued to the expected publisher. An artifact labelled `UNSIGNED-DEV` is
for controlled testing only and must not be promoted as a release.

Before installing a signed release:

1. Open `windows-build-manifest.json` and check `signed` and
   `signatureStatus`.
2. In PowerShell, calculate the installer hash:

   ```powershell
   Get-FileHash -Algorithm SHA256 -LiteralPath '.\EDGEOS Setup 0.1.0.exe'
   ```

3. Compare it with the matching installer line in `SHA256SUMS.txt`. Obtain the
   expected checksum through a trusted channel when possible.
4. Verify Authenticode rather than relying only on the manifest:

   ```powershell
   Get-AuthenticodeSignature -LiteralPath '.\EDGEOS Setup 0.1.0.exe' |
     Format-List Status,StatusMessage,SignerCertificate
   ```

5. Run `EDGEOS Setup <version>.exe`, choose the installation directory if
   desired, and complete the wizard.

The installer normally needs no administrator rights and installs for the
current Windows user. It creates Start Menu and desktop shortcuts. It does not
launch EDGEOS automatically when the wizard closes; start it from either
shortcut.

The exact default installation directory is selected by NSIS for the current
user and is normally under `%LOCALAPPDATA%\Programs`. Do not assume a fixed
path when scripting installation or removal.

## Windows SmartScreen and code signing

Pull-request installers are unsigned development artifacts. For an unsigned
build, Windows can show **Windows protected your PC** and **Unknown publisher**.
This is expected for development output, but it is not proof that the file is
safe and is not an acceptable production release state.

Do not bypass SmartScreen, disable antivirus, or work around organizational
security controls to install a distributed EDGEOS artifact. Use a release with
the expected publisher and a valid signature. If you intentionally built an
unsigned installer yourself for local development, follow your organization's
development-software policy and validate its checksum from your own build
output.

For a signed release, `Get-AuthenticodeSignature` should return `Valid` and the
expected publisher for all three executable layers: the installer, installed
`EDGEOS.exe`, and bundled `edgeos-api.exe`. A production release must use
SHA-256 Authenticode signing with an RFC 3161 timestamp and verify both the
signature and timestamp on installed payloads. Authenticode establishes
publisher and file integrity; it does not guarantee immediate SmartScreen
reputation. A new OV certificate or rarely downloaded release can still receive
a reputation warning, and EV or managed signing can improve reputation without
guaranteeing it.

## What runs on the computer

Starting EDGEOS launches one desktop process and two owned child services:

| Component | Address | Purpose |
| --- | --- | --- |
| Next.js interface | OS-assigned `127.0.0.1` candidate port per launch | Renders the packaged application and owns the browser-visible origin |
| FastAPI research API | Separate OS-assigned `127.0.0.1` candidate port | Runs local calculations and provider diagnostics |

Both services bind to IPv4 loopback only; they are not intended to be reachable
from another device. The desktop host obtains ephemeral candidate ports,
launches the owned processes, and requires each health response to return the
per-launch instance nonce before opening the window. A candidate can be claimed
in the short interval before child startup; that race fails closed. Browser
requests use the same-origin `/api/edgeos/...` Next.js backend-for-frontend
rather than connecting directly to the internal API port. It exposes an
explicit route-and-method allowlist for market reads, calculations, and
simulated paper-workflow mutations. Cross-origin requests and operations not on
that allowlist are rejected. A separate cryptographically random credential is
created on every application launch, passed only to the two owned child
processes, and attached to internal API requests by the server-side gateway.
FastAPI rejects business routes without it. The credential is not placed in a
`NEXT_PUBLIC_*` variable, renderer API, command line, or log. The gateway never
exposes a real-money execution route. Every accepted API response must carry the
matching versioned
`demo` runtime-mode contract before the interface will display it.

EDGEOS prevents a second application instance and performs graceful
child-process cleanup when the application exits normally. The API also watches
the Electron parent and exits if that owner dies; Electron owns the Next.js
utility process. Forced-crash cleanup still belongs in the Windows acceptance
suite. If either child stops after startup, EDGEOS closes rather than continuing
to display potentially stale data.

The window remembers its last normal size, position, and maximized state. Saved
geometry is validated and clamped to an available monitor so unplugging a
display does not strand the application off screen. No background service or
startup task remains after the window and its owned processes exit.

The packaged Electron window has Node integration disabled, context isolation
and sandboxing enabled, permission requests denied, and navigation restricted
to the local EDGEOS origin. External HTTPS URLs without embedded
username/password userinfo may open in the default browser. Arbitrary downloads
are denied; only the named paper-ledger CSV, account-data JSON, and
`.edgeos-backup` local-workspace exports from the active local app origin can
open an explicit native save dialog.
Production fuses disable RunAsNode, `NODE_OPTIONS`, and CLI
inspection, require the application ASAR, enable ASAR integrity validation, and
encrypt Chromium cookies with Windows cryptography. Before launching either
child, the host verifies every staged web/API file against a SHA-256 manifest
embedded in the packaged ASAR. Electron's embedded-ASAR integrity fuse and the
signed executable protect that manifest in a signed release.

### Device-local data and offline behavior

Electron supplies a fixed application-owned location to the API; browsers and
API callers cannot choose a database path. Current-user files are normally:

```text
%APPDATA%\EDGEOS\data\edgeos-local.sqlite3
%APPDATA%\EDGEOS\window-state.json
%APPDATA%\EDGEOS\logs\edgeos-desktop.log
```

SQLite writes use transactions, a schema version, an integrity digest for each
state namespace, and an integrity check when the database opens. A failed write
rolls the in-memory mutation back. A corrupted or unsupported database is
preserved and startup fails visibly; EDGEOS does not silently replace it with a
fresh profitable-looking portfolio. This database contains simulated workflow
state only. Provider credentials, Supabase administrative keys, signing
material, session tokens, and live market history are not stored there.

The bundled demo workspace and local paper ledger can be opened without internet
access after installation. Cloud authentication, cross-device sync, official
provider queries, and external source links require connectivity and their
normal configuration. No cached or fixture quote is relabelled as live while
offline.

There is no native Windows toast integration in this release. Alert rules are
local configuration only because no authoritative evaluator/delivery worker is
bundled. Electron continues to deny notification permission so deterministic
demo alerts cannot appear as misleading operating-system notifications.

### Local security boundary

Loopback reduces network exposure but is not, by itself, an authentication
boundary. OS-assigned ephemeral candidate ports, the startup nonce check, the
separate per-launch API credential, and the constrained same-origin proxy reduce
accidental cross-talk, direct API mutation, browser-origin attacks, and
fixed-port service impersonation. A raw process already running as the same
Windows user can still discover and imitate local HTTP traffic, so these
controls do not protect a computer controlled by same-user malware or an
administrator.

The per-user installation can also be modified by malware or another process
running as that Windows user. Code signing protects the distribution path and
detects modified signed files; it does not make a compromised endpoint safe.
Never write provider keys, signing credentials, Supabase secrets, session
tokens, or authorization headers to the diagnostic log. Review and redact logs
before sharing them.

## Demo build versus a configured build

### Standard artifact

The checked-in workflow builds with:

```text
NEXT_PUBLIC_APP_MODE=demo
NEXT_PUBLIC_API_URL=/api/edgeos
NEXT_PUBLIC_DESKTOP_BUILD=true
```

The desktop host always starts the bundled API in demo mode and does not pass
private provider credentials into it. Therefore, downloading the normal
artifact requires no account or API key and does not provide a live scanner.
The packaged runtime ignores attempts to turn it into live mode with
`EDGEOS_MODE=live`.

Changing a label or environment variable does not turn the standard desktop
artifact into a live deployment. It does not launch the background ingestion
worker, connect to a hosted live database, or receive server-held provider
secrets; those components require separately configured hosted infrastructure.
Its local SQLite file is only the durable device-local simulated workspace.

### Supabase-connected private build

`NEXT_PUBLIC_*` values are compiled into the packaged Next.js bundle. They must
be set **before building the installer**; setting them after installation does
not reconfigure an existing installer.

To compile account support into a private build, use a Supabase project whose
migrations have already been applied, then run from PowerShell 7:

```powershell
$env:NEXT_PUBLIC_SUPABASE_URL = 'https://YOUR_PROJECT.supabase.co'
$env:NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY = 'sb_publishable_...'
$env:NEXT_PUBLIC_APP_MODE = 'demo'
$env:NEXT_PUBLIC_DESKTOP_BUILD = 'true'
pwsh -File .\scripts\build-windows-installer.ps1
```

`NEXT_PUBLIC_DESKTOP_BUILD=true` is mandatory for every Electron artifact and
the supported build script forces it. Do not disable the flag to expose signup,
OAuth, or recovery controls: those flows require a browser-to-app handoff that
this release does not implement.

The Supabase URL and publishable key are public client configuration. They do
not bypass row-level security. Never place a Supabase secret/service-role key in
a `NEXT_PUBLIC_*` variable, the repository, an installer, or a release artifact.

Direct email/password sign-in can use this configuration for an already
verified account. The Windows build disables account creation, password-reset
links, email-link confirmation, and Google OAuth because those flows leave the
Electron window for the system browser. Its cookie jar is different from
Electron's, and the interface uses an unpredictable local port on each launch.
Adding a fixed `127.0.0.1` URL to the Supabase allow list cannot establish a
desktop session.

Google OAuth, automatic post-confirmation login, and email-link session transfer
are **unavailable in the Windows client** until EDGEOS implements and tests a
custom-protocol PKCE/deep-link handoff. Create, verify, and recover accounts in
the hosted web application, then use direct email/password sign-in in the
configured private desktop build. Keep the hosted HTTPS URL as the Supabase Site
URL and configure its exact callback, confirmation, and password-update paths as
described in the main README.

The publishable configuration adds existing-account sign-in, RLS-protected
profile/preferences, and an RLS-scoped account-data export. It does not enable
live scanner ingestion or switch the bundled demo API to the hosted durable
portfolio, watchlist, alert, or paper-P&L repositories. Use the hosted web
deployment for those configured live services; the standard desktop runtime is
intentionally isolated in demo mode. The bundled API's local SQLite state and
the signed-in Supabase profile are separate stores; this release does not merge
or cross-device-sync the local paper ledger.

Account deletion uses the server-only `SUPABASE_SECRET_KEY`. The public desktop
installer deliberately does not embed that key, so privileged account deletion
is unavailable in a normal desktop build. Keep privileged operations on a
trusted hosted backend rather than distributing an administrative credential
to client computers.

The Odds API and other private provider credentials are also not compiled into
the standard installer. The desktop package has no Windows Credential Manager
integration in this release. Its workspace is forced demo even though provider
diagnostics may describe or explicitly probe implemented public adapters; those
diagnostics do not populate a live scanner. Operate credentialed ingestion on
trusted server infrastructure and do not redistribute an installer containing
private API secrets.

See [Supabase setup in the main README](../README.md#supabase-setup) for database
migration, SMTP, and provider configuration.

## Build from source on Windows

The complete installer must be built on Windows; PyInstaller does not
cross-compile the bundled API executable.

Required tools:

- x64 Windows and PowerShell 7 (`pwsh`)
- Node.js 22 or newer and npm 10 or newer
- Python 3.12
- [`uv`](https://docs.astral.sh/uv/)

From the repository root:

```powershell
pwsh -File .\scripts\build-windows-installer.ps1
```

The script performs locked dependency installation, the Next.js production
build, PyInstaller API packaging, resource staging, a staged-payload smoke
test, desktop type checking and tests, NSIS packaging, a full `EDGEOS.exe`
launch/shutdown smoke test, exact payload rehashing, Electron fuse inspection,
signature inspection, and checksum/manifest generation. It then silently
installs the actual NSIS artifact into a unique per-user test directory, checks
the installed payload against the verified package, checks both shortcuts,
launches the installed app, reruns the same installer through the
upgrade/reinstall path, verifies the SQLite workspace and a separate data
sentinel survived, launches it again, uninstalls it, and verifies that binaries
and shortcuts were removed while device-local user data remained. Acceptance
launches use a unique temporary `APPDATA`/`LOCALAPPDATA` tree, which is removed
only after retention has been proved; the build does not open or modify an
existing EDGEOS workspace. The test also refuses to overwrite a pre-existing
EDGEOS installation or shortcut, so release builds still belong in a clean
Windows test account or VM. The staged Windows runtime test writes a local
setting, stops and restarts the bundled API against the same SQLite path, and
verifies the value was restored. This automated check is a same-version
reinstall test, not an N-1-to-N migration matrix.

Its primary outputs are:

```text
apps/desktop/release/EDGEOS Setup <version>.exe
artifacts/windows/EDGEOS Setup <version>.exe
artifacts/windows/windows-build-manifest.json
artifacts/windows/SHA256SUMS.txt
```

An installer blockmap or channel metadata file is copied to `artifacts/windows`
only when electron-builder happens to generate it. EDGEOS has no automatic
updater and does not depend on either file.

`artifacts/windows` is the verified publishable set. The current package version
is read from `apps/desktop/package.json`; change it intentionally before a
release. The script deletes stale generated release/artifact directories only
after validating that they are inside the expected repository paths.

For an already prepared workspace, `-SkipDependencyInstall` skips dependency
installation. `-PayloadOnly` builds and smoke-tests without creating NSIS
output. `-PackageOnly` packages already staged resources. These switches are
intended for CI and advanced troubleshooting; the command above is the normal
reproducible build.

`-SkipInstalledAcceptance` exists only for local packaging diagnostics where a
developer cannot use a clean Windows account. It is rejected together with
`-RequireSignature`, and any output built with it is not a fully tested release
candidate. The checked-in Windows CI workflow never enables it.

Running `npm run desktop:dist:win` directly is not a replacement for the build
script because it expects a staged standalone web server and
`resources/api/edgeos-api.exe` to exist already.

## Build a signed installer

Use an Authenticode code-signing certificate that is authorized for the EDGEOS
publisher. Modern publicly trusted signing keys are normally hardware-backed;
prefer Azure Trusted Signing, an HSM, or a managed signing service such as
KeyLocker for production. The exportable PFX path below is a compatibility path
for controlled development/release environments, not the preferred long-term
key-custody model. Keep the PFX outside the repository and supply its absolute
local path only for the build process:

```powershell
$env:CSC_LINK = (Resolve-Path 'C:\secure\edgeos-signing.pfx').Path
$env:CSC_KEY_PASSWORD = '<certificate-password>'
$env:WINDOWS_EXPECTED_SIGNER_THUMBPRINT = '<40-character-approved-thumbprint>'
try {
  pwsh -File .\scripts\build-windows-installer.ps1 -RequireSignature
} finally {
  Remove-Item Env:CSC_LINK -ErrorAction SilentlyContinue
  Remove-Item Env:CSC_KEY_PASSWORD -ErrorAction SilentlyContinue
  Remove-Item Env:WINDOWS_EXPECTED_SIGNER_THUMBPRINT -ErrorAction SilentlyContinue
}
```

With `-RequireSignature`, the build fails unless the installer, packaged
`EDGEOS.exe`, and bundled `edgeos-api.exe` have valid Authenticode signatures
from one signer and an RFC 3161 timestamp on each. It also requires
`WINDOWS_EXPECTED_SIGNER_THUMBPRINT` (or the equivalent script parameter) to pin
that signer to the approved 40-character certificate thumbprint.

The build also accepts electron-builder's `WIN_CSC_LINK` and
`WIN_CSC_KEY_PASSWORD` aliases. It normalizes them to the same local-PFX signing
path used for the bundled API. If generic and Windows-specific variables are
both present but disagree, the build fails before packaging instead of risking
a partially or inconsistently signed release.

Create a protected GitHub Environment named `windows-signing`, require release
reviewers, and store these as **environment secrets** (not repository secrets):

- `WINDOWS_CERTIFICATE_BASE64`: base64-encoded PFX bytes
- `WINDOWS_CERTIFICATE_PASSWORD`: PFX password

Both must be configured together. The workflow decodes the certificate only in
the runner's temporary directory and deletes it in cleanup. Also create the
protected-environment variable `WINDOWS_SIGNER_THUMBPRINT` with the approved
certificate thumbprint. CI refuses a signed build without this pin. Keep
approval restricted to trusted non-PR revisions: the checked-in workflow does
not reference the signing environment for pull-request jobs, while the
protected environment's reviewer policy is the ultimate release control. The
same secret and variable names must not exist at repository or organization
scope, because GitHub contexts can otherwise fall back to those broader scopes.
Enable required reviewers, prevent self-review where available, restrict the
environment to protected release tags/default-branch deployments, and protect
the `v*` tag namespace against unauthorized creation or modification. The
build removes signing credential environment variables from runtime smoke-test
children, and workflow cleanup deletes the encrypted PFX from the temporary
runner path. Exportable base64 PFX storage is less
robust than managed/hardware-backed signing and should be replaced before broad
production distribution.

## GitHub Actions release artifact

The workflow is `.github/workflows/windows-installer.yml`. It runs for relevant
pull requests, version tags matching `v*`, and manual `workflow_dispatch` runs.
It uploads:

```text
edgeos-windows-installer-SIGNED-<workflow-run-number>-<run-attempt>
edgeos-windows-installer-UNSIGNED-DEV-<workflow-run-number>-<run-attempt>
```

with 30-day retention. Version tags fail closed when signing secrets or the
approved signer pin are absent, when the tag version differs from the desktop
package, or when the tagged commit is outside the default branch. Manual builds
are restricted to the default branch. Pull requests and unsigned manual builds
are always labelled `UNSIGNED-DEV`. The signing environment should require a
human reviewer to inspect the exact source revision before releasing secrets.
The workflow does not create a GitHub Release or publish to an update server.
Promoting the verified artifact to a durable release channel is a separate,
authorized release operation.

## Uninstall

1. Sign out first when the computer is shared or will leave your control.
2. Exit EDGEOS and wait for its local services to stop.
3. Open **Windows Settings → Apps → Installed apps**.
4. Find **EDGEOS**, open its menu, and select **Uninstall**.
5. Complete the uninstaller.

The uninstaller removes the installed executables, shortcuts, and application
files. It deliberately retains per-user data at `%APPDATA%\EDGEOS`
(`deleteAppDataOnUninstall` is disabled), including the local SQLite paper
workspace, window state, browser/session storage, and diagnostic logs. This is a
data-retention decision so an uninstall/reinstall or upgrade does not erase the
user's simulated ledger. It does not delete cloud data from Supabase and the
uninstaller does not leave a service running.

For a complete local cleanup, uninstall first, ensure EDGEOS is closed, and then
remove the EDGEOS directory under `%APPDATA%` using File Explorer. Review the
directory before deleting it because this permanently removes the local paper
ledger and settings. Delete cloud account data separately through an authorized
server-side account-deletion flow.

## Troubleshooting

### EDGEOS cannot allocate a local endpoint

EDGEOS asks the OS for ephemeral loopback candidate ports on each launch. Restart once in case a
short-lived process won a port race. If the error repeats, inspect the diagnostic
log and local endpoint-protection/firewall policy. Do not terminate an
unfamiliar process or weaken endpoint security to make EDGEOS start.

### Startup fails or the window closes

The native error dialog shows the log location. It is normally:

```text
%APPDATA%\EDGEOS\logs\edgeos-desktop.log
```

Open it with:

```powershell
Get-Content "$env:APPDATA\EDGEOS\logs\edgeos-desktop.log" -Tail 200
```

Look for a missing bundled resource, port conflict, local firewall denial, or a
child process that exited. Reinstall if `server.js` or `edgeos-api.exe` is
missing. Do not download replacement executables from an unofficial source.

### Windows or antivirus quarantines the bundled API

PyInstaller executables can trigger heuristic scanning. Verify the installer
checksum and Authenticode status, then use the antivirus product's normal review
or enterprise allow-list process. Do not disable protection globally. A release
publisher should submit signed false positives to the security vendor.

### Local paper data cannot be opened

Exit EDGEOS and preserve `%APPDATA%\EDGEOS\data` before troubleshooting. A
database integrity or schema error is intentionally fail-closed; do not delete
the database merely to make the application start. Use **Settings → Data &
security** to download a versioned `.edgeos-backup` before a risky change and to
restore a known-good backup. Restore validates the complete document and payload
hashes before replacing the simulated local namespaces in one transaction. The
backup is not encrypted or authenticated, so store it as sensitive user data.

### Sign-in returns to an error page

Confirm that the installer was built with the correct publishable URL and key,
that the Supabase migration was applied, and that the account was already
verified through the hosted application. Only direct email/password sign-in is
supported in Electron. Public configuration is baked into the installer, so
correcting it requires a rebuild and reinstall.

### Google sign-in opens the browser but does not return to EDGEOS

This is a known limitation, not an allow-list configuration error. The Electron
host opens Google in the system browser, and the callback establishes browser
cookies rather than an Electron session. Google OAuth is unsupported in the
Windows client until a custom-protocol PKCE/deep-link flow is implemented and
tested. Use direct email/password sign-in in a configured private build or the
hosted web application.

### An update does not appear automatically

Automatic update checks and an update server are not implemented. Download a
new verified installer and run it manually. The uninstaller retains app data,
but export important paper-ledger data before an upgrade. Local persistence
remains device-only and simulated.

## Current limitations

- x64 Windows installer only; no ARM64 package is produced.
- Local source builds are unsigned development output unless a signing
  certificate is supplied. Use `-RequireSignature` when a missing certificate
  must fail instead of producing explicitly labelled development output.
- No automatic update client or production update endpoint.
- Manual local-workspace backup/restore is available, but there is no scheduler,
  rotation policy, cloud destination, or encrypted backup vault.
- The local SQLite database is device-only and not encrypted at rest. It must not
  contain provider, Supabase, signing, or real-money account credentials.
- OS-assigned ephemeral loopback candidates and nonce-authenticated startup
  checks still do not protect a compromised Windows account.
- Forced-crash and orphan cleanup are implemented defensively but still require
  clean-machine Windows acceptance testing before broad distribution.
- Standard build is demo mode, not a durable live scanner.
- No secure desktop store for provider or Supabase administrative secrets.
- No supported Google OAuth, automatic email-confirmation login, or email-link
  session transfer in Electron.
- The standard desktop runtime is demo-only and neither bundles Postgres nor
  configures the live durable portfolio, watchlist, alert-rule, settings, or
  analytics repositories. Those repositories exist for a separately operated
  hosted/live deployment; they are not enabled by this installer.
- No real-money order placement or automated wagering.

## Production acceptance checklist

Before distributing a production installer, test at minimum:

- clean Windows 11 install as a standard user, upgrade, downgrade policy, and
  uninstall with both retained-data and explicit-wipe expectations;
- publisher, SHA-256 signature, RFC 3161 timestamp, and signature validity on
  the installer, installed Electron executable, and bundled API;
- normal shutdown, force-kill, orphan cleanup, crash recovery, and both port
  collision and local-impostor rejection;
- hostile `Host`/`Origin` requests, CSP/navigation/download handling, and
  offline behavior;
- artifact and log scans for Supabase secrets, provider keys, signing material,
  tokens, and authorization headers; and
- supported authentication flows using separate system-browser and Electron
  cookie stores.

The automated Windows workflow now covers the normal silent per-user install,
same-version upgrade/reinstall, installed-runtime launch, Start Menu and desktop
shortcuts, retained-data uninstall policy, and removal of installed files. A
human clean-machine pass is still required for the assisted installer wizard,
Windows Settings UI, SmartScreen/Defender experience, downgrade policy, and
interactive standard-user behavior.

