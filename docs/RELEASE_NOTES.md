# EDGEOS desktop release notes

Release candidate date: 2026-10-20  
Desktop package version: `0.1.0`

## Outcome

This source revision upgrades the existing EDGEOS web/API product into a
self-contained Windows desktop release candidate. Electron launches the packaged
Next.js interface and PyInstaller FastAPI executable; the user does not start a
development server or open a browser.

The Windows installer remains a **paper-trading and research product**. Its market
fixture is visibly labelled demo data, it places no bet or order, and the advised
real-money allocation is **$0**.

## Delivered in this desktop pass

- x64 per-user NSIS configuration with Start Menu and desktop shortcuts, normal
  Windows window controls, application icon, clean application uninstall, and
  retained user data for upgrades/reinstall;
- packaged standalone Next.js and FastAPI children on OS-assigned IPv4-loopback
  candidate ports, owned and stopped by Electron;
- sandboxed renderer, context isolation, disabled Node integration, navigation/
  permission controls, native-save-dialog allowlisting for the named paper-ledger
  CSV, account-data JSON, and local-workspace backup exports, Electron fuses,
  staged-resource SHA-256 checks, and single-instance handling;
- a startup nonce plus an independent random per-launch API credential. The
  server-side BFF attaches that credential and FastAPI rejects unauthenticated
  desktop business requests;
- remembered window size, normal position, and maximized state, with validation
  and monitor-bound clamping after a display is removed;
- durable Local Mode SQLite at
  `%APPDATA%\EDGEOS\data\edgeos-local.sqlite3` for simulated paper positions,
  fills, settlements, cancellations, ledger activity, saved opportunities,
  watchlists, alert rules, and user settings;
- versioned/strict local schema, WAL journaling, full synchronous commits,
  transaction rollback on save failure, exact Decimal/UTC serialization,
  bounded checksummed payloads, path/symlink confinement, and fail-closed startup
  that preserves corrupt or unsupported databases;
- a polished two-/three-way calculator accepting decimal, American, fractional,
  and binary-contract asks, with user-entered fee/slippage/capital-cap assumptions
  and authoritative FastAPI stake balancing;
- refreshed dark desktop navigation, data-health/refresh status, responsive
  layouts, and removal of fabricated alert/watchlist counts and silent private
  workflow fallbacks; and
- a Windows CI/build path that performs source tests, payload staging, bundled
  runtime checks, NSIS packaging, signature/fuse/integrity verification, and
  manifest/checksum creation. Its runtime smoke test proves a saved local
  setting survives a bundled API stop/restart. The release gate also installs
  the actual NSIS artifact into a unique per-user test directory, verifies the
  installed payload and shortcuts, launches it before and after a same-version
  upgrade/reinstall pass, confirms isolated local test data survives that pass,
  then uninstalls and confirms the binaries/shortcuts are removed while local
  data is retained. The test uses a temporary AppData tree and does not open a
  developer's existing EDGEOS workspace; N-1-to-N testing remains a release
  matrix requirement.

## Installer output and trust

Build on Windows x64 with PowerShell 7:

```powershell
pwsh -File .\scripts\build-windows-installer.ps1
```

Verified publishable files are staged at:

```text
artifacts/windows/EDGEOS Setup <version>.exe
artifacts/windows/windows-build-manifest.json
artifacts/windows/SHA256SUMS.txt
```

The source audit and validation session ran on Linux, which cannot produce the
PyInstaller Windows API executable. No `.exe` was generated, signed, installed,
or manually exercised here. Use the checked-in `windows-latest` GitHub Actions
workflow or the documented Windows command. A distributed release must be
Authenticode-signed and checked against the approved signer and SHA-256 manifest;
an `UNSIGNED-DEV` artifact is only for controlled development testing.

## Data and upgrade behavior

Closing/reopening EDGEOS or installing an upgrade retains its device-local
simulated records. Uninstall removes the application but deliberately retains
`%APPDATA%\EDGEOS`; complete deletion is a separate explicit operator action.
The local database does not sync to another device and is separate from a
configured Supabase account.

SQLite is **not encrypted at rest**. EDGEOS does not place provider keys,
Supabase tokens/administrative keys, signing material, sportsbook credentials,
or real-money account records in that database. A transactionally consistent
online-backup primitive exists internally. Settings now provides a versioned
logical `.edgeos-backup` download and a size-bounded, integrity-checked,
transactional restore for simulated local state. Automatic scheduling, rotation,
encryption, and remote retention are not included in this release candidate.

## Deliberate limitations

- The standard installer is demo market-data mode and does not run the continuous
  ingestion worker or connect the hosted live research repositories.
- Public provider adapters and official-shaped fixture coverage exist, but live
  access depends on current upstream availability, terms, geography, credentials,
  and plan entitlement. Provider credentials are not embedded in the installer.
- The live worker persists normalized evidence but does not yet generate match
  candidates, strict opportunities, or alert events. A live scanner will normally
  remain empty unless a trusted external process writes evidenced opportunities.
- Alert-rule CRUD is available; no authoritative evaluator, Windows toast,
  email, SMS, or push delivery is shipped.
- Desktop signup, password-recovery/confirmation links, Google OAuth return, and
  automatic account deletion are unavailable until a secure desktop deep-link
  and trusted hosted privileged flow are implemented.
- Manual local backup/restore is available; scheduled, rotated, encrypted, and
  remotely retained backups remain a follow-up.
- There is no automatic updater, ARM64 installer, real-money execution, automated
  wagering, funding, or provider-account credential sharing.

For the full feature/status inventory, operating workflow, and paper-capital
guidance, read [HANDOFF.md](HANDOFF.md). For build, signing, installation,
verification, troubleshooting, and removal, read
[WINDOWS_INSTALLER.md](WINDOWS_INSTALLER.md).
