"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { ArrowRight, CheckCircle2, LoaderCircle } from "lucide-react";
import { createBrowserSupabaseClient } from "@/lib/supabase/client";
import { safeRedirectPath } from "@/lib/safe-redirect";
import { Button } from "@/components/ui";

const field = "focus-ring mt-2 h-11 w-full rounded-xl border border-[#293e5b] bg-[#0d1829] px-3 text-sm text-[#e7effa] placeholder:text-[#52647d]";

export function authCallbackErrorMessage(code: string | null): string {
  if (code === "configuration") {
    return "Account sign-in is unavailable because Supabase is not configured.";
  }
  if (code === "callback") {
    return "The sign-in callback could not be verified. Start the sign-in flow again.";
  }
  if (code === "confirmation") {
    return "The email confirmation link is invalid or expired. Request a new link and try again.";
  }
  return "";
}

export function AuthForm({ mode }: { mode: "login" | "signup" | "forgot" | "update" }) {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const supabase = createBrowserSupabaseClient();
  const desktopBuild = process.env.NEXT_PUBLIC_DESKTOP_BUILD === "true";
  const desktopLinkFlowUnavailable = desktopBuild && mode !== "login";
  // Callback routes intentionally expose only a small error-code vocabulary.
  // Never reflect arbitrary query text into the authentication UI.
  const callbackError = authCallbackErrorMessage(params.get("error"));
  const displayedError = error || callbackError;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setMessage("");
    if (desktopLinkFlowUnavailable) {
      setError("Account creation and recovery links require the hosted web application in this desktop release.");
      return;
    }
    if (!supabase) {
      setError("Supabase is not configured. Add the public URL and publishable key to enable accounts.");
      return;
    }
    setLoading(true);
    try {
      if (mode === "login") {
        const { error: signInError } = await supabase.auth.signInWithPassword({ email, password });
        if (signInError) throw signInError;
        router.replace(safeRedirectPath(params.get("next")));
        router.refresh();
      } else if (mode === "signup") {
        const { error: signUpError } = await supabase.auth.signUp({
          email,
          password,
          options: { emailRedirectTo: `${location.origin}/auth/callback?next=/` },
        });
        if (signUpError) throw signUpError;
        setMessage("Check your email to verify the account before signing in.");
      } else if (mode === "forgot") {
        const { error: resetError } = await supabase.auth.resetPasswordForEmail(email, {
          redirectTo: `${location.origin}/auth/update-password`,
        });
        if (resetError) throw resetError;
        setMessage("If the account exists, a password reset link is on its way.");
      } else {
        const { error: updateError } = await supabase.auth.updateUser({ password });
        if (updateError) throw updateError;
        setMessage("Password updated. You can return to your workspace.");
      }
    } catch (value) {
      setError(value instanceof Error ? value.message : "Authentication failed");
    } finally {
      setLoading(false);
    }
  };

  const google = async () => {
    if (desktopBuild) {
      setError("Google sign-in needs a verified browser-to-app callback and is unavailable in this desktop release.");
      return;
    }
    if (!supabase) {
      setError("Supabase is not configured.");
      return;
    }
    setLoading(true);
    const { error: oauthError } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${location.origin}/auth/callback?next=/` },
    });
    if (oauthError) {
      setError(oauthError.message);
      setLoading(false);
    }
  };

  return <form onSubmit={submit} className="mt-7 space-y-4">
    {desktopLinkFlowUnavailable && <p className="rounded-xl border border-[#634d29] bg-[#2c2413]/55 p-3 text-xs leading-5 text-[#d5b56b]">This desktop release can sign in to an existing email/password account. Create or recover an account in the hosted web application.</p>}
    {mode !== "update" && <label className="block text-xs font-medium text-[#899ab0]">Email address<input required type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} className={field} placeholder="you@example.com" /></label>}
    {mode !== "forgot" && <label className="block text-xs font-medium text-[#899ab0]">{mode === "update" ? "New password" : "Password"}<input required minLength={8} type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} value={password} onChange={(event) => setPassword(event.target.value)} className={field} placeholder="At least 8 characters" /></label>}
    {mode === "login" && !desktopBuild && <div className="text-right"><Link href="/forgot-password" className="text-xs font-medium text-[#6da5fb] hover:text-[#8ab7fb]">Forgot password?</Link></div>}
    {mode === "login" && desktopBuild && <p className="text-right text-xs text-[#71839b]">Recovery links are available in the hosted web app.</p>}
    {displayedError && <p role="alert" className="rounded-xl border border-[#623641] bg-[#321a22]/60 p-3 text-xs leading-5 text-[#ee91a0]">{displayedError}</p>}
    {message && <p className="flex gap-2 rounded-xl border border-[#285646] bg-[#123027]/60 p-3 text-xs leading-5 text-[#72d6b5]"><CheckCircle2 className="mt-0.5 size-4 shrink-0" />{message}</p>}
    <Button type="submit" disabled={loading || desktopLinkFlowUnavailable} className="h-11 w-full border-[#4f8cff] bg-[#4f8cff] text-white hover:bg-[#659dff]">{loading ? <LoaderCircle className="size-4 animate-spin" /> : <>{mode === "login" ? "Sign in" : mode === "signup" ? "Create account" : mode === "forgot" ? "Send reset link" : "Update password"}<ArrowRight className="size-4" /></>}</Button>
    {(mode === "login" || mode === "signup") && <>
      <div className="flex items-center gap-3 text-[10px] uppercase tracking-[.12em] text-[#54677f]"><span className="h-px flex-1 bg-[#233650]" />Optional<span className="h-px flex-1 bg-[#233650]" /></div>
      <Button type="button" onClick={google} disabled={loading || desktopBuild} className="h-11 w-full">{desktopBuild ? "Google sign-in unavailable on desktop" : "Continue with Google"}</Button>
      {!desktopBuild && <p className="text-center text-xs text-[#71839b]">{mode === "login" ? "New to EDGEOS?" : "Already have an account?"} <Link href={mode === "login" ? "/signup" : "/login"} className="font-semibold text-[#6da5fb]">{mode === "login" ? "Create account" : "Sign in"}</Link></p>}
    </>}
  </form>;
}
