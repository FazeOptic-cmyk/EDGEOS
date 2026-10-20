import { randomBytes } from "node:crypto";
import {
  appendFileSync, existsSync, mkdirSync, readFileSync, renameSync, rmSync, statSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { spawn, type ChildProcess } from "node:child_process";
import {
  app, BrowserWindow, dialog, ipcMain, Menu, screen, session, shell, utilityProcess,
  type IpcMainInvokeEvent, type Session, type UtilityProcess,
} from "electron";
import {
  allocateRuntimeEndpoints, assertResourcesExist, hasReadinessIdentity, isSafeExternalUrl,
  isTrustedAppNavigation, presentStartupError, redactLogMessage, waitForJsonHealth,
  classifyDesktopExport,
  verifyResourceIntegrity, type DesktopResources, type RuntimeEndpoints, StartupError,
} from "./runtime";
import {
  MINIMUM_WINDOW_HEIGHT, MINIMUM_WINDOW_WIDTH, parsePersistedWindowState,
  visibleWindowBounds, windowStateDocument,
} from "./window-state";
import {
  hostedSessionPartition, isConnectionSettingsSender, LOCAL_DEMO_CONNECTION,
  readDesktopConnection, saveDesktopConnection, verifyHostedConnection, type DesktopConnection,
} from "./connection";
import { CONNECTION_SETTINGS_URL } from "./connection-settings";

// Keep Electron's runtime identity (and therefore its userData directory)
// deterministic instead of deriving it from the scoped workspace package name.
app.setName("EDGEOS");

const instanceNonce = randomBytes(32).toString("base64url");
const shutdownToken = randomBytes(32).toString("base64url");
const apiToken = randomBytes(32).toString("base64url");
const smokeTestNonce = (() => {
  const prefix = "--edgeos-smoke-test=";
  const argument = process.argv.find((value) => value.startsWith(prefix));
  if (!argument) return null;
  const nonce = argument.slice(prefix.length);
  return /^[A-Za-z0-9-]{20,100}$/u.test(nonce) ? nonce : null;
})();
const LOG_LIMIT_BYTES = 4 * 1024 * 1024;

let mainWindow: BrowserWindow | null = null;
let apiProcess: ChildProcess | null = null;
let webProcess: UtilityProcess | null = null;
let apiExitCode: number | null | undefined;
let webExitCode: number | null | undefined;
let stopping = false;
let servicesReady = false;
let logFile = "";
let endpoints: RuntimeEndpoints | null = null;
let windowStateFile = "";
let localDataRoot = "";
let localDataPath = "";
let pendingWindowStateWrite: NodeJS.Timeout | null = null;
let connectionFile = "";
let activeConnection: DesktopConnection = { ...LOCAL_DEMO_CONNECTION };
let settingsWindow: BrowserWindow | null = null;
let changingConnection = false;
let recoveringConnection = false;
let restarting = false;
const securedSessions = new WeakSet<Session>();

function rotateLog(path: string): void {
  try {
    if (!existsSync(path) || statSync(path).size <= LOG_LIMIT_BYTES) return;
    const backup = `${path}.1`;
    rmSync(backup, { force: true });
    renameSync(path, backup);
  } catch {
    // A locked log is non-fatal; append remains best effort.
  }
}

function log(message: string): void {
  try {
    appendFileSync(logFile, `[${new Date().toISOString()}] ${redactLogMessage(message)}\n`, "utf8");
  } catch {
    // Native error dialogs still work if logging is unavailable.
  }
}

function resources(): DesktopResources {
  const root = app.isPackaged ? process.resourcesPath : join(__dirname, "..", "..", "resources");
  return {
    root,
    webDirectory: join(root, "web"),
    webServer: join(root, "web", "server.js"),
    apiExecutable: join(root, "api", process.platform === "win32" ? "edgeos-api.exe" : "edgeos-api"),
    integrityManifest: join(__dirname, "resource-integrity.json"),
  };
}

function sanitizedEnvironment(overrides: Record<string, string>): Record<string, string> {
  const allowed = [
    "ALLUSERSPROFILE", "APPDATA", "ComSpec", "CommonProgramFiles", "CommonProgramFiles(x86)",
    "CommonProgramW6432", "LOCALAPPDATA", "NUMBER_OF_PROCESSORS", "OS", "Path", "PATH", "PATHEXT",
    "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "PROCESSOR_LEVEL", "PROCESSOR_REVISION",
    "ProgramData", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "SystemDrive", "SystemRoot",
    "TEMP", "TMP", "USERPROFILE", "windir", "LANG", "TZ",
  ];
  const result: Record<string, string> = {};
  for (const key of allowed) {
    const value = process.env[key];
    if (value !== undefined) result[key] = value;
  }
  return { ...result, ...overrides };
}

function watchUnexpectedExit(name: string, code: number | null): void {
  log(`${name} exited with code ${code ?? "unknown"}`);
  if (stopping || !servicesReady) return;
  servicesReady = false;
  dialog.showErrorBox("EDGEOS service stopped", `${name} stopped unexpectedly. EDGEOS will close to avoid displaying stale data.\n\nDiagnostic log: ${logFile}`);
  void stopServices().finally(() => app.quit());
}

function startApi(resource: DesktopResources, runtime: RuntimeEndpoints): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(resource.apiExecutable, ["--port", String(runtime.apiPort)], {
      cwd: dirname(resource.apiExecutable),
      env: sanitizedEnvironment({
        EDGEOS_DESKTOP_API_PORT: String(runtime.apiPort), EDGEOS_DESKTOP_PARENT_PID: String(process.pid),
        EDGEOS_DESKTOP_SHUTDOWN_TOKEN: shutdownToken, EDGEOS_DESKTOP_INSTANCE_NONCE: instanceNonce,
        EDGEOS_DESKTOP_API_TOKEN: apiToken, EDGEOS_LOCAL_DATA_ROOT: localDataRoot,
        EDGEOS_LOCAL_DATA_PATH: localDataPath,
        EDGEOS_ENV: "desktop", EDGEOS_MODE: "demo", EDGEOS_CORS_ORIGINS: runtime.webOrigin,
        WEB_ORIGIN: runtime.webOrigin,
      }),
      shell: false, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
    });
    apiProcess = child;
    apiExitCode = undefined;
    child.stdout?.on("data", (chunk: Buffer) => log(`[api] ${chunk.toString().trimEnd()}`));
    child.stderr?.on("data", (chunk: Buffer) => log(`[api:error] ${chunk.toString().trimEnd()}`));
    child.once("error", (error) => reject(new StartupError(`The EDGEOS API could not launch: ${error.message}`, "Reinstall EDGEOS or ask your administrator to allow the bundled API executable.")));
    child.once("spawn", resolve);
    child.once("exit", (code) => { apiExitCode = code; watchUnexpectedExit("The EDGEOS API", code); });
  });
}

function startWeb(resource: DesktopResources, runtime: RuntimeEndpoints): void {
  const child = utilityProcess.fork(resource.webServer, [], {
    cwd: resource.webDirectory,
    env: sanitizedEnvironment({
      EDGEOS_ENV: "desktop", EDGEOS_DESKTOP_INSTANCE_NONCE: instanceNonce,
      EDGEOS_DESKTOP_API_TOKEN: apiToken,
      EDGEOS_DESKTOP_WEB_ORIGIN: runtime.webOrigin,
      EDGEOS_INTERNAL_API_ORIGIN: runtime.apiOrigin, HOSTNAME: "127.0.0.1",
      NEXT_TELEMETRY_DISABLED: "1", NODE_ENV: "production", PORT: String(runtime.webPort),
    }),
    serviceName: "EDGEOS Web", stdio: "pipe",
  });
  webProcess = child;
  webExitCode = undefined;
  child.stdout?.on("data", (chunk: Buffer) => log(`[web] ${chunk.toString().trimEnd()}`));
  child.stderr?.on("data", (chunk: Buffer) => log(`[web:error] ${chunk.toString().trimEnd()}`));
  child.once("exit", (code) => { webExitCode = code; watchUnexpectedExit("The EDGEOS web service", code); });
}

function waitForChildExit(child: ChildProcess, timeoutMs: number): Promise<boolean> {
  if (child.exitCode !== null) return Promise.resolve(true);
  return new Promise((resolve) => {
    const onExit = (): void => { clearTimeout(timer); resolve(true); };
    const timer = setTimeout(() => { child.off("exit", onExit); resolve(false); }, timeoutMs);
    child.once("exit", onExit);
  });
}

async function forceStopProcessTree(child: ChildProcess): Promise<void> {
  if (!child.pid || child.exitCode !== null) return;
  if (process.platform === "win32") {
    await new Promise<void>((resolve) => {
      const killer = spawn("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], {
        env: sanitizedEnvironment({}), windowsHide: true, stdio: "ignore", shell: false,
      });
      killer.once("close", () => resolve());
      killer.once("error", () => resolve());
    });
    return;
  }
  child.kill("SIGKILL");
}

async function stopApi(): Promise<void> {
  const child = apiProcess;
  apiProcess = null;
  if (!child || child.exitCode !== null || !child.pid) return;
  let acknowledged = false;
  if (endpoints) {
    try {
      const response = await fetch(`${endpoints.apiOrigin}/api/v1/desktop/shutdown`, {
        method: "POST", headers: { "x-edgeos-desktop-token": shutdownToken }, signal: AbortSignal.timeout(1_000),
      });
      acknowledged = response.ok;
    } catch {
      log("The API did not acknowledge graceful shutdown; using process-tree termination fallback");
    }
  }
  if (acknowledged && await waitForChildExit(child, 3_000)) return;
  await forceStopProcessTree(child);
}

async function stopServices(): Promise<void> {
  if (stopping) return;
  stopping = true;
  servicesReady = false;
  webProcess?.kill();
  webProcess = null;
  await stopApi();
  log("Desktop services stopped");
}

function configureSecurity(browserSession: Session, trustedOrigin: string): void {
  if (securedSessions.has(browserSession)) return;
  securedSessions.add(browserSession);
  browserSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  browserSession.setPermissionCheckHandler(() => false);
  browserSession.on("will-download", (event, item, contents) => {
    const allowed = classifyDesktopExport(
      item.getURL(), item.getMimeType(), item.getFilename(), trustedOrigin,
    );
    if (!allowed || !mainWindow || contents.id !== mainWindow.webContents.id) {
      event.preventDefault();
      log("Blocked a download outside the allowlisted EDGEOS export flow");
      return;
    }
    item.pause();
    const owner = mainWindow;
    void dialog.showSaveDialog(owner, {
      title: `Export EDGEOS ${allowed.label}`,
      defaultPath: join(app.getPath("downloads"), allowed.filename),
      buttonLabel: "Export",
      filters: [{ name: allowed.label, extensions: [allowed.extension] }],
      properties: ["showOverwriteConfirmation", "createDirectory"],
    }).then(({ canceled, filePath }) => {
      if (canceled || !filePath || item.getState() !== "progressing") {
        if (item.getState() === "progressing") item.cancel();
        return;
      }
      const suffix = `.${allowed.extension}`;
      const safePath = filePath.toLowerCase().endsWith(suffix) ? filePath : `${filePath}${suffix}`;
      item.setSavePath(safePath);
      item.resume();
    }).catch(() => {
      if (item.getState() === "progressing") item.cancel();
      log("The EDGEOS export save dialog could not be opened");
    });
  });
}

function openExternal(url: string): void {
  void shell.openExternal(url).catch(() => log("The default browser could not open an external HTTPS link"));
}

function readWindowState() {
  try {
    return parsePersistedWindowState(readFileSync(windowStateFile, "utf8"));
  } catch {
    return null;
  }
}

function persistWindowState(window: BrowserWindow): void {
  if (window.isDestroyed()) return;
  try {
    const document = windowStateDocument(window.getNormalBounds(), window.isMaximized());
    const temporary = `${windowStateFile}.tmp`;
    writeFileSync(temporary, document, { encoding: "utf8", mode: 0o600 });
    rmSync(windowStateFile, { force: true });
    renameSync(temporary, windowStateFile);
  } catch {
    log("Window state could not be saved; defaults will be used on the next launch");
  }
}

function scheduleWindowStateWrite(window: BrowserWindow): void {
  if (pendingWindowStateWrite) clearTimeout(pendingWindowStateWrite);
  pendingWindowStateWrite = setTimeout(() => {
    pendingWindowStateWrite = null;
    persistWindowState(window);
  }, 300);
}

function restartDesktop(): void {
  if (restarting) return;
  restarting = true;
  app.relaunch();
  void stopServices().finally(() => app.quit());
}

function settingsSenderAllowed(event: IpcMainInvokeEvent): boolean {
  return isConnectionSettingsSender(
    event.sender.id,
    settingsWindow && !settingsWindow.isDestroyed() ? settingsWindow.webContents.id : null,
    event.senderFrame === event.sender.mainFrame,
    event.senderFrame?.url ?? "",
    CONNECTION_SETTINGS_URL,
  );
}

function configureConnectionSettings(): void {
  ipcMain.handle("edgeos:connection", async (event, request: unknown) => {
    if (!settingsSenderAllowed(event)) throw new Error("Connection settings request rejected.");
    if (!request || typeof request !== "object" || Array.isArray(request)) throw new Error("Invalid settings request.");
    const command = request as Record<string, unknown>;
    if (command.action === "read") {
      try {
        const connection = readDesktopConnection(connectionFile);
        return { origin: connection.mode === "hosted-live" ? connection.origin : null };
      } catch {
        return { origin: null, error: "Saved settings are invalid. Select a connection below to repair them." };
      }
    }
    if (command.action !== "connect" && command.action !== "local-demo") throw new Error("Unknown settings request.");
    if (changingConnection || restarting) return { ok: false, error: "A connection change is already in progress." };
    changingConnection = true;
    try {
      const next = command.action === "connect"
        ? await verifyHostedConnection(command.origin)
        : { ...LOCAL_DEMO_CONNECTION };
      // Closing/replacing the settings window cancels an in-flight verification.
      if (!settingsSenderAllowed(event)) return { ok: false, error: "Connection change canceled." };
      saveDesktopConnection(connectionFile, next);
      setImmediate(restartDesktop);
      return { ok: true };
    } catch (error) {
      return {
        ok: false,
        error: error instanceof StartupError
          ? presentStartupError(error)
          : "Connection settings could not be saved. Check that your EDGEOS user-data folder is writable.",
      };
    } finally {
      changingConnection = false;
    }
  });
}

async function openConnectionSettings(): Promise<void> {
  if (settingsWindow && !settingsWindow.isDestroyed()) {
    settingsWindow.show();
    settingsWindow.focus();
    return;
  }
  const settingsSession = session.fromPartition("edgeos-connection-settings");
  settingsSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  settingsSession.setPermissionCheckHandler(() => false);
  const window = new BrowserWindow({
    width: 720, height: 690, minWidth: 600, minHeight: 600,
    parent: mainWindow ?? undefined, modal: mainWindow !== null, show: false,
    backgroundColor: "#080D18", title: "EDGEOS connection settings",
    webPreferences: {
      session: settingsSession, preload: join(__dirname, "connection-preload.js"),
      nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true,
      allowRunningInsecureContent: false, webviewTag: false, navigateOnDragDrop: false,
      devTools: !app.isPackaged, spellcheck: false,
    },
  });
  settingsWindow = window;
  window.setMenu(null);
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  window.webContents.on("will-redirect", (event) => event.preventDefault());
  window.webContents.on("will-frame-navigate", (event) => event.preventDefault());
  const closed = new Promise<void>((resolve) => window.once("closed", () => {
    if (settingsWindow === window) settingsWindow = null;
    resolve();
  }));
  try {
    await window.loadURL(CONNECTION_SETTINGS_URL);
    if (!window.isDestroyed()) window.show();
  } catch {
    window.destroy();
    dialog.showErrorBox("Connection settings unavailable", "EDGEOS could not open its connection settings. Reinstall the application if this continues.");
  }
  await closed;
}

async function useLocalDemo(): Promise<void> {
  if (restarting || changingConnection) return;
  const { response } = await dialog.showMessageBox({
    type: "question", title: "Use local demo?",
    message: "Restart EDGEOS with the offline local demo?",
    detail: "Your local demo data is preserved. Saved hosted sign-ins remain in their separate server sessions.",
    buttons: ["Restart in local demo", "Cancel"], defaultId: 1, cancelId: 1,
  });
  if (response !== 0 || restarting || changingConnection) return;
  try {
    saveDesktopConnection(connectionFile, LOCAL_DEMO_CONNECTION);
    restartDesktop();
  } catch {
    dialog.showErrorBox("Connection settings unavailable", "The local demo selection could not be saved.");
  }
}

async function reconnectHosted(): Promise<void> {
  if (activeConnection.mode !== "hosted-live" || !mainWindow || changingConnection || restarting) return;
  changingConnection = true;
  try {
    await verifyHostedConnection(activeConnection.origin);
    if (mainWindow && !mainWindow.isDestroyed()) await mainWindow.loadURL(activeConnection.origin);
  } catch (error) {
    dialog.showErrorBox("EDGEOS could not reconnect", `${presentStartupError(error)}\n\nUse Connection → Server settings to change servers or return to local demo.`);
  } finally {
    changingConnection = false;
  }
}

function updateApplicationMenu(): void {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      label: "&Connection", submenu: [
        { label: activeConnection.mode === "hosted-live" ? `Live server: ${activeConnection.origin}` : "Local demo (offline)", enabled: false },
        { type: "separator" },
        { label: "Server settings…", accelerator: "CmdOrCtrl+,", click: () => { void openConnectionSettings(); } },
        { label: "Reconnect to live server", enabled: activeConnection.mode === "hosted-live", click: () => { void reconnectHosted(); } },
        { label: "Use local demo…", enabled: activeConnection.mode === "hosted-live", click: () => { void useLocalDemo(); } },
        { type: "separator" },
        { role: "quit" },
      ],
    },
    { role: "editMenu" },
    {
      label: "&View", submenu: [
        { role: "reload" }, { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" },
        { type: "separator" }, { role: "togglefullscreen" },
      ],
    },
  ]));
}

async function openMainWindow(trustedOrigin: string, browserSession: Session): Promise<void> {
  const savedState = readWindowState();
  const bounds = visibleWindowBounds(
    savedState?.bounds ?? null,
    screen.getAllDisplays().map((display) => display.workArea),
  );
  const window = new BrowserWindow({
    ...bounds, minWidth: MINIMUM_WINDOW_WIDTH, minHeight: MINIMUM_WINDOW_HEIGHT, show: false,
    backgroundColor: "#080D18", autoHideMenuBar: false,
    title: activeConnection.mode === "hosted-live" ? "EDGEOS — Live server" : "EDGEOS — Local demo",
    webPreferences: {
      session: browserSession,
      contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true,
      allowRunningInsecureContent: false, devTools: !app.isPackaged, navigateOnDragDrop: false,
      spellcheck: false, webviewTag: false,
    },
  });
  mainWindow = window;
  window.on("page-title-updated", (event) => {
    event.preventDefault();
    window.setTitle(activeConnection.mode === "hosted-live"
      ? `EDGEOS — ${activeConnection.origin} (live server)` : "EDGEOS — Local demo");
  });
  window.once("ready-to-show", () => {
    if (savedState?.maximized) window.maximize();
    window.show();
  });
  window.on("move", () => scheduleWindowStateWrite(window));
  window.on("resize", () => scheduleWindowStateWrite(window));
  window.on("maximize", () => scheduleWindowStateWrite(window));
  window.on("unmaximize", () => scheduleWindowStateWrite(window));
  window.on("close", () => persistWindowState(window));
  window.on("closed", () => {
    if (pendingWindowStateWrite) clearTimeout(pendingWindowStateWrite);
    pendingWindowStateWrite = null;
    mainWindow = null;
  });
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (isSafeExternalUrl(url)) openExternal(url);
    return { action: "deny" };
  });
  const guardNavigation = (event: Electron.Event, url: string): void => {
    if (isTrustedAppNavigation(url, trustedOrigin)) return;
    event.preventDefault();
    if (isSafeExternalUrl(url)) openExternal(url);
  };
  window.webContents.on("will-navigate", guardNavigation);
  window.webContents.on("will-redirect", guardNavigation);
  window.webContents.on("will-frame-navigate", (event) => {
    if (!isTrustedAppNavigation(event.url, trustedOrigin)) event.preventDefault();
  });
  try {
    await window.loadURL(trustedOrigin);
  } catch (error) {
    window.destroy();
    throw new StartupError(`The EDGEOS interface could not load: ${error instanceof Error ? error.message : String(error)}`, "Restart EDGEOS. If the problem continues, review the desktop diagnostic log.");
  }
}

async function startLocalDesktop(): Promise<void> {
  const bundled = resources();
  await assertResourcesExist(bundled);
  await verifyResourceIntegrity(bundled);
  log("Bundled resource integrity checks passed");
  endpoints = await allocateRuntimeEndpoints();

  await startApi(bundled, endpoints);
  await waitForJsonHealth({
    url: `${endpoints.apiOrigin}/api/v1/desktop/readiness`, serviceName: "The EDGEOS API",
    validate: (body) => hasReadinessIdentity(body, "edgeos-api", instanceNonce),
    abortReason: () => apiExitCode === undefined ? null : `code ${apiExitCode ?? "unknown"}`,
  });
  log("API nonce readiness check passed");

  startWeb(bundled, endpoints);
  await waitForJsonHealth({
    url: `${endpoints.webOrigin}/api/desktop-health`, serviceName: "The EDGEOS interface",
    validate: (body) => hasReadinessIdentity(body, "edgeos-web", instanceNonce),
    abortReason: () => webExitCode === undefined ? null : `code ${webExitCode ?? "unknown"}`,
  });
  if (apiExitCode !== undefined || webExitCode !== undefined) throw new StartupError("An EDGEOS service exited before the interface opened.", "Review the desktop diagnostic log.");
  log("Web nonce readiness check passed");
  servicesReady = true;
  configureSecurity(session.defaultSession, endpoints.webOrigin);
  updateApplicationMenu();
  await openMainWindow(endpoints.webOrigin, session.defaultSession);
  if (smokeTestNonce) {
    log(`Packaged Electron smoke test passed (${smokeTestNonce})`);
    await stopServices();
    app.exit(0);
  }
}

async function startConfiguredDesktop(): Promise<void> {
  // Keep a failed hosted window/load or a closed recovery settings window from
  // triggering window-all-closed before the native recovery decision is made.
  recoveringConnection = true;
  try {
    while (!stopping && !restarting) {
      let failure: unknown;
      try {
        activeConnection = smokeTestNonce ? { ...LOCAL_DEMO_CONNECTION } : readDesktopConnection(connectionFile);
        updateApplicationMenu();
        if (activeConnection.mode === "local-demo") {
          await startLocalDesktop();
          return;
        }
        await verifyHostedConnection(activeConnection.origin);
        const browserSession = session.fromPartition(hostedSessionPartition(activeConnection.origin));
        configureSecurity(browserSession, activeConnection.origin);
        await openMainWindow(activeConnection.origin, browserSession);
        log("Hosted live server mode contract verified; no local services were started");
        return;
      } catch (error) {
        // Bundled service failures use the existing fatal startup path. Hosted
        // failures remain recoverable without changing the saved data mode.
        if (endpoints || smokeTestNonce) throw error;
        failure = error;
      }
      const { response } = await dialog.showMessageBox({
        type: "error", title: "EDGEOS connection unavailable",
        message: "EDGEOS could not open the selected connection.",
        detail: presentStartupError(failure),
        buttons: ["Retry", "Connection settings", "Use local demo", "Quit"],
        defaultId: 1, cancelId: 3, noLink: true,
      });
      if (response === 1) await openConnectionSettings();
      else if (response === 2) {
        saveDesktopConnection(connectionFile, LOCAL_DEMO_CONNECTION);
      } else if (response === 3) {
        app.quit();
        return;
      }
    }
  } finally {
    recoveringConnection = false;
  }
}

async function startDesktop(): Promise<void> {
  app.setAppLogsPath(join(app.getPath("appData"), "EDGEOS", "logs"));
  logFile = join(app.getPath("logs"), "edgeos-desktop.log");
  mkdirSync(dirname(logFile), { recursive: true });
  localDataRoot = app.getPath("userData");
  localDataPath = join(localDataRoot, "data", "edgeos-local.sqlite3");
  mkdirSync(dirname(localDataPath), { recursive: true });
  windowStateFile = join(app.getPath("userData"), "window-state.json");
  connectionFile = join(app.getPath("userData"), "connection.json");
  rotateLog(logFile);
  log(`Starting EDGEOS ${app.getVersion()}`);
  configureConnectionSettings();
  await startConfiguredDesktop();
}

app.on("web-contents-created", (_event, contents) => {
  contents.on("will-attach-webview", (event) => event.preventDefault());
});

const ownsInstance = app.requestSingleInstanceLock();
if (!ownsInstance) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });
  app.whenReady().then(startDesktop).catch(async (error: unknown) => {
    log(`Startup failed: ${error instanceof Error ? error.stack ?? error.message : String(error)}`);
    await stopServices();
    if (smokeTestNonce) {
      app.exit(1);
      return;
    }
    dialog.showErrorBox("EDGEOS couldn't start", `${presentStartupError(error)}\n\nDiagnostic log: ${logFile}`);
    app.quit();
  });
}

app.on("before-quit", (event) => {
  if (stopping) return;
  event.preventDefault();
  void stopServices().finally(() => app.quit());
});

app.on("window-all-closed", () => {
  if (!recoveringConnection) app.quit();
});
