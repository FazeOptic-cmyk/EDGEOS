import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsClient } from "./settings-client";

const mocks = vi.hoisted(() => ({
  getUserSettings: vi.fn(),
  updateUserSettings: vi.fn(),
  exportLocalWorkspaceBackup: vi.fn(),
  restoreLocalWorkspaceBackup: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("@/lib/supabase/client", () => ({
  createBrowserSupabaseClient: () => null,
}));

vi.mock("@/lib/workflow-api", () => ({
  getUserSettings: mocks.getUserSettings,
  updateUserSettings: mocks.updateUserSettings,
  exportLocalWorkspaceBackup: mocks.exportLocalWorkspaceBackup,
  restoreLocalWorkspaceBackup: mocks.restoreLocalWorkspaceBackup,
  LOCAL_WORKSPACE_BACKUP_FILENAME: "edgeos-local-workspace-backup.edgeos-backup",
}));

function renderSettings() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SettingsClient configured={false} appMode="demo" internalApiConfigured={false} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_DESKTOP_BUILD", "true");
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn(() => "blob:http://localhost/export"),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: vi.fn(),
  });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  mocks.getUserSettings.mockReset().mockResolvedValue({
    timezone: "UTC",
    currency: "USD",
    default_allocation: "250",
    maximum_quote_age_seconds: 120,
    demo_mode: true,
    verified_matches_only: true,
  });
  mocks.updateUserSettings.mockReset().mockImplementation(async (input) => ({
    timezone: "UTC",
    currency: "USD",
    default_allocation: input.default_allocation,
    maximum_quote_age_seconds: 120,
    demo_mode: true,
    verified_matches_only: input.verified_matches_only,
  }));
  mocks.exportLocalWorkspaceBackup.mockReset().mockResolvedValue(new Blob(["backup"]));
  mocks.restoreLocalWorkspaceBackup.mockReset().mockResolvedValue({
    status: "restored", restored_namespaces: ["private_user_state"], restart_required: false,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

describe("desktop local preferences", () => {
  it("hydrates and saves through the local private-data API without claiming cloud sync", async () => {
    renderSettings();
    fireEvent.click(screen.getByRole("button", { name: "Preferences" }));

    const capital = await screen.findByLabelText("Default simulated capital");
    await waitFor(() => expect(capital).toHaveValue(250));
    fireEvent.change(capital, { target: { value: "375" } });
    fireEvent.click(screen.getByRole("button", { name: "Save local preferences" }));

    await waitFor(() => expect(mocks.updateUserSettings).toHaveBeenCalledWith({
      default_allocation: "375",
      verified_matches_only: true,
    }));
    expect(await screen.findByText(/saved to this device's local EDGEOS workspace/i)).toBeInTheDocument();
    expect(screen.queryByText(/synced across devices/i)).not.toBeInTheDocument();
  });

  it("does not describe an ordinary demo API workspace as device-local persistence", async () => {
    vi.stubEnv("NEXT_PUBLIC_DESKTOP_BUILD", "false");
    renderSettings();
    fireEvent.click(screen.getByRole("button", { name: "Preferences" }));

    expect(await screen.findByText("Stored in the current demonstration API workspace")).toBeInTheDocument();
    expect(screen.queryByText(/Persisted by the device-local/i)).not.toBeInTheDocument();
  });

  it("requires a selected backup and typed confirmation before local restore", async () => {
    renderSettings();
    fireEvent.click(screen.getByRole("button", { name: "Data & security" }));

    const file = new File(["portable"], "workspace.edgeos-backup", {
      type: "application/vnd.edgeos.workspace-backup+json",
    });
    const restore = screen.getByRole("button", { name: "Restore" });
    expect(restore).toBeDisabled();
    fireEvent.change(screen.getByLabelText("EDGEOS backup"), { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText("Type RESTORE"), { target: { value: "RESTORE" } });
    fireEvent.click(restore);

    await waitFor(() => expect(mocks.restoreLocalWorkspaceBackup).toHaveBeenCalledWith(file));
    expect(await screen.findByText(/workspace restored/i)).toBeInTheDocument();
  });

  it("does not claim a desktop backup was written before the native save dialog completes", async () => {
    renderSettings();
    fireEvent.click(screen.getByRole("button", { name: "Data & security" }));
    fireEvent.click(screen.getByRole("button", { name: "Download backup" }));

    await waitFor(() => expect(mocks.exportLocalWorkspaceBackup).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("status")).toHaveTextContent(/backup prepared.*native Save dialog/i);
    expect(screen.queryByText(/backup downloaded/i)).not.toBeInTheDocument();
  });
});
