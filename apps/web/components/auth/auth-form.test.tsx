import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthForm, authCallbackErrorMessage } from "./auth-form";

const navigationMocks = vi.hoisted(() => ({ query: "" }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(navigationMocks.query),
}));

describe("desktop authentication gating", () => {
  beforeEach(() => {
    navigationMocks.query = "";
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllEnvs();
  });

  it("keeps existing email/password login but disables browser callback flows", () => {
    vi.stubEnv("NEXT_PUBLIC_DESKTOP_BUILD", "true");
    render(<AuthForm mode="login" />);

    expect(screen.getByRole("button", { name: /sign in/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /google sign-in unavailable/i })).toBeDisabled();
    expect(screen.queryByRole("link", { name: /forgot password/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /create account/i })).not.toBeInTheDocument();
    expect(screen.getByText(/recovery links are available in the hosted web app/i)).toBeInTheDocument();
  });

  it("disables signup and directs desktop users to the hosted application", () => {
    vi.stubEnv("NEXT_PUBLIC_DESKTOP_BUILD", "true");
    render(<AuthForm mode="signup" />);

    expect(screen.getByRole("button", { name: /create account/i })).toBeDisabled();
    expect(screen.getByText(/create or recover an account in the hosted web application/i)).toBeInTheDocument();
  });

  it("leaves hosted-web authentication controls available", () => {
    vi.stubEnv("NEXT_PUBLIC_DESKTOP_BUILD", "false");
    render(<AuthForm mode="login" />);

    expect(screen.getByRole("button", { name: /continue with google/i })).toBeEnabled();
    expect(screen.getByRole("link", { name: /forgot password/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /create account/i })).toBeInTheDocument();
  });

  it("renders a safe callback diagnosis without reflecting arbitrary query text", () => {
    navigationMocks.query = "error=confirmation";
    vi.stubEnv("NEXT_PUBLIC_DESKTOP_BUILD", "false");
    render(<AuthForm mode="login" />);

    expect(screen.getByRole("alert")).toHaveTextContent(/confirmation link is invalid or expired/i);
    expect(authCallbackErrorMessage("<script>unsafe</script>")).toBe("");
    expect(screen.queryByText(/unsafe/i)).not.toBeInTheDocument();
  });
});
