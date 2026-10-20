import { contextBridge, ipcRenderer } from "electron";

// This preload is attached only to the bundled settings document, never to the
// local web app or hosted content. No arbitrary channels, files, or shell access.
contextBridge.exposeInMainWorld("edgeosConnection", {
  read: () => ipcRenderer.invoke("edgeos:connection", { action: "read" }),
  connect: (origin: string) => ipcRenderer.invoke("edgeos:connection", { action: "connect", origin }),
  localDemo: () => ipcRenderer.invoke("edgeos:connection", { action: "local-demo" }),
});
