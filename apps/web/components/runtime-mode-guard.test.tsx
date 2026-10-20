import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RuntimeModeGuard } from "./runtime-mode-guard";

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("RuntimeModeGuard", () => {
  it("blocks demo rendering when the BFF positively reports a live API", async () => {
    vi.stubEnv("NEXT_PUBLIC_APP_MODE", "demo");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: { code: "mode_mismatch" } }),
      {
        status: 503,
        headers: {
          "Content-Type": "application/json",
          "X-EDGEOS-Mode": "live",
          "X-EDGEOS-Mode-Contract": "1",
        },
      },
    )));

    render(<RuntimeModeGuard><div>protected results</div></RuntimeModeGuard>);

    expect(await screen.findByRole("alert")).toHaveTextContent(/data mode configuration error/i);
    expect(screen.queryByText("protected results")).not.toBeInTheDocument();
  });

  it("keeps a clearly labelled demo workspace usable when the API is offline", async () => {
    vi.stubEnv("NEXT_PUBLIC_APP_MODE", "demo");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));

    render(<RuntimeModeGuard><div>offline demo workspace</div></RuntimeModeGuard>);

    await waitFor(() => expect(screen.getByText("offline demo workspace")).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
