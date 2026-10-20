# Security model

EDGEOS is a read-only market-research and simulated paper-trading application.
There is no order-placement or real-money custody path. That smaller scope does
not remove the need to protect sessions, user research, provider API keys, and
the integrity of market evidence.

## Trust boundaries

1. The browser is untrusted. It receives a Supabase publishable key, never a
   secret/service-role key, database password, provider master key, or provider
   credential recovered from storage.
2. The Next.js server is the browser-facing BFF. It validates the user session,
   same-origin mutations, route allowlist, body/response size, and response shape.
3. FastAPI verifies Supabase JWT claims and derives ownership from the token
   subject. A request body cannot select another user.
4. Postgres enforces RLS and grants in addition to API checks.
5. The worker and service role are privileged ingestion components and must run
   only in a protected server environment.
6. Provider APIs are external and untrusted inputs. Responses are validated and
   retained with provenance; error text is not blindly returned to browsers.
7. The packaged desktop runtime is a per-Windows-user local application. Its
   SQLite file and Electron profile rely on that user's Windows profile ACLs;
   they are not a boundary against malware, an administrator, or another
   process already running as the same Windows user.

## Authentication and sessions

- Supabase Auth handles email/password hashing, verification, password reset,
  optional hosted Google OAuth, token issuance, and revocation.
- The web uses `@supabase/ssr` cookie-backed sessions and refresh middleware.
- API live-mode routes validate bearer JWTs against Supabase signing metadata.
- Demo mode uses a fixed server-side local identity; it is not a production auth
  bypass. Browser/CLI demo development defaults to resettable process memory,
  while the packaged desktop explicitly supplies its per-user SQLite path.
- Account deletion requires a token issued within the preceding 15 minutes, then
  a server-only Supabase secret performs global sign-out and exact-user deletion.
- Production deployments must use HTTPS. Cookies and tokens must never be logged.

The legacy `SUPABASE_JWT_SECRET` path is compatibility-only. Prefer current
Supabase asymmetric signing/JWKS validation and remove the legacy value once the
project no longer needs it.

## Row-level security and grants

All exposed schema tables enable RLS. Shared reference and market-research rows
have explicit read policies. Private records use `auth.uid() = user_id`, with
matching `WITH CHECK` predicates. Nested financial records verify ownership via
the parent portfolio/position. Browser grants are narrowed by table/column:

- users may update their safe profile and settings fields;
- users may manage their own watchlists, filters, alerts, saved opportunities,
  and notes where granted;
- financial fills, settlements, ledger entries, ingestion records, global
  opportunities, and match decisions are service-controlled;
- connection metadata is owner-readable, but write operations use the server;
- `private.provider_credentials` and worker leases are not accessible to
  `anon` or `authenticated`.

Run Supabase security/performance advisors after each migration. RLS is defense
in depth; service endpoints must still check the authenticated owner.

## Provider credential envelope

The implemented credential cipher/repository primitives use `AES-256-GCM`:

- a cryptographically random 12-byte nonce is created for each encryption;
- authenticated additional data binds the envelope to the user ID, provider code,
  and key version;
- the credential object is serialized deterministically before encryption;
- the database stores ciphertext, nonce, algorithm, key version, and a field-name
  manifest in the service-only `private` schema;
- public/owner-visible metadata contains only a masked suffix and status;
- a ciphertext moved to another user/provider fails authentication;
- corrupt ciphertext, a wrong key, or unknown key version fails closed.

`EDGEOS_CREDENTIAL_MASTER_KEY` must be URL-safe/base64 text that decodes to
exactly 32 random bytes. It belongs in a managed secret store and never in Git,
the database, a `NEXT_PUBLIC_*` variable, a URL, analytics, or support chat.
`EDGEOS_CREDENTIAL_KEY_VERSION` identifies the active key. Rotation requires a
controlled decrypt/re-encrypt process before retiring the old key; changing the
environment value alone makes old records unreadable.

The Odds API is the only current connection form designed to accept a secret
(`api_key`). Public Kalshi and Polymarket configuration does not request private
trading credentials. The registered live-only FastAPI routes provide owner-
scoped add/replace/test/enable/disable/delete operations. They fail closed unless
authentication, Postgres/migrations, and the cipher key are configured. The
worker does not yet consume these per-user connections.

## Redaction and safe errors

- Known secret values are removed from connection-test errors.
- Common `api_key`, bearer, token, secret, and password forms are redacted.
- Database connection failures cross the connection boundary as a generic error;
  the DSN is never formatted into an application response.
- Browser connection routes forward stable safe error codes, not arbitrary
  provider response bodies.
- Account exports may include safe connection metadata but exclude private
  credential ciphertext and original values.
- Structured logs must record provider/error class and timestamp, never request
  authorization headers or provider secrets.

## Browser-to-API proxy and SSRF controls

The same-origin BFF exposes an explicit path/method allowlist, including the
required read and paper-workflow mutations. It does not accept a destination URL
from the browser. The internal origin:

- must be absolute `http` or `https`;
- cannot contain username/password, path, query, or fragment;
- is configured server-side;
- is combined only with allowlisted path segments;
- uses `redirect: error`, timeouts, and bounded bodies/responses;
- checks expected desktop host/nonces in desktop mode;
- checks same-origin `Origin` on state-changing requests;
- sends `Cache-Control: no-store` for private connection responses.

Keep the internal API on a private network where possible. Do not make the BFF an
arbitrary URL proxy.

## Mode and data-integrity controls

Web and API publish an explicit `demo|live` contract. The client sends
`X-EDGEOS-Client-Mode`; the API returns `X-EDGEOS-Mode` and contract version. A
mismatch returns 409. Live screens do not fall back to demo opportunities,
positions, or P&L. Unknown timestamps, fees, rules, and depth stay unknown rather
than becoming zero/fresh/unlimited.

This prevents a configuration error from presenting sample profitability as live
evidence. It does not prove that an upstream quote can fill.

## Desktop controls

The Electron renderer is sandboxed with context isolation and no Node integration.
Navigation/new-window behavior is restricted. Local Next/API processes bind only
to loopback candidate ports and share a per-launch nonce checked by health routes.
Electron also generates a separate random API token for each launch. The token is
passed only to the local API and Next.js server processes; the server-side BFF
adds it to allowlisted upstream requests, while FastAPI rejects business requests
without it using a constant-time comparison. It is not a `NEXT_PUBLIC_*` value,
renderer configuration, command-line argument, or logged field. Readiness is
exempt so the owning process can prove which child it launched; orderly shutdown
uses an independent per-launch secret.

The loopback token prevents an unrelated local web page from calling the API
directly and prevents clients from bypassing the browser-facing gateway. It is
defense in depth, not a claim of isolation from code already executing as the
same OS user. A same-user process may be able to discover and call the local BFF,
or inspect or manipulate that user's processes and files.

Only one instance runs, and child processes are cleaned up on normal exit.
Electron denies permission requests, arbitrary downloads, webviews, untrusted
navigation, and non-HTTPS external links. Downloads are limited to the named
CSV-ledger, JSON-account export, and `.edgeos-backup` local-workspace blob
formats from the active app origin and always use an explicit native save
dialog. Electron fuses and staged resource hashes are verified during
packaging.

### Desktop local data

The desktop host supplies an absolute SQLite path beneath Electron's
`app.getPath("userData")`; a browser request cannot select it. The persistence
layer resolves and confines database and backup destinations to that application
data root, uses parameterized SQL, a versioned strict schema, foreign-key checks,
WAL journaling, full synchronous writes, bounded payloads, atomic transactions,
and rollback of in-memory mutations when a durable write fails. On POSIX test or
development hosts it also requires owner-only directory/file modes where the OS
supports them. On Windows, the files inherit the current user's profile ACLs.

SQLite stores paper positions, fills, settlements and ledger entries, watchlists,
alerts, settings, and saved-opportunity metadata. Money is serialized without
binary floating-point conversion and timestamps retain UTC timezone information.
It must never contain a provider API key, Supabase access/refresh token,
service-role key, credential-encryption key, signing key, sportsbook login, or
real-money account credential. The standard desktop installer remains demo/local
mode, and configured provider secrets stay on trusted hosted infrastructure.

The SQLite database is **not encrypted at rest**. Its payload SHA-256 value catches
accidental damage but is not a MAC and does not make locally modified data
trustworthy. Windows full-disk encryption and a protected OS account are separate
operator controls. A future feature that accepts a local provider secret must use
Windows Credential Manager or a reviewed DPAPI-backed broker and must not put the
plaintext or a colocated decryption key in SQLite, logs, child command lines, or
the renderer.

Startup performs an SQLite integrity check and fails closed while preserving the
original file if the database is corrupt or its schema is unsupported. The code
contains a transactionally consistent SQLite online-backup primitive so a WAL
database is never backed up with a naive file copy. The desktop UI also exports
a versioned logical `.edgeos-backup`; restore bounds its size, rejects duplicate
or unknown namespaces, checks document and payload SHA-256 values, applies the
same domain decoders used at startup, and only then replaces all local namespaces
in one transaction. SHA-256 detects damage but is not authenticity or encryption.
There is no automatic schedule, rotation, or protected remote destination, so
recovery still depends on the operator keeping and testing backups.
Uninstall intentionally retains `%APPDATA%\EDGEOS`; deleting that directory is a
separate, irreversible user action.

The standard installer is demo-only, contains no server secret, does not install a
worker, and has no updater. A private Supabase-connected build may contain only
public URL/publishable-key configuration. Signed artifacts must match the approved
certificate thumbprint and published SHA-256 manifest.

## Deployment controls

- terminate public traffic with HTTPS;
- set exact CORS and application origins;
- keep API/worker/database credentials in the platform secret manager;
- use TLS to Postgres (`DATABASE_SSLMODE=require` in hosted live mode);
- run migrations before new code;
- isolate worker/service-role credentials from the web runtime where possible;
- cap provider requests and alert on unexpected consumption;
- back up the database and test restore/credential-rotation procedures;
- run dependency, container, Supabase advisor, and secret-scanning checks;
- keep raw data only as long as permitted by provider terms/licensing;
- disable a connection immediately if a key may have leaked, rotate it at the
  provider, then replace the encrypted value.

## Out of scope / not a security claim

- No penetration test or third-party security audit is included.
- Fixture and unit tests do not prove a hosted deployment is hardened.
- The application does not bypass geography, identity, age, eligibility, or plan
  restrictions.
- Informational public data access does not authorize trading.
- There is no real-money execution, sportsbook-password storage, or account
  credential sharing.
- A schema with RLS does not make unconnected private repositories functional.

Report vulnerabilities privately to the repository owner. Do not include keys,
tokens, user data, or exploitable production details in a public issue.
