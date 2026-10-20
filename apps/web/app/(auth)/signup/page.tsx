import { Suspense } from "react";
import { AuthForm } from "@/components/auth/auth-form";
import { AuthShell } from "@/components/auth/auth-shell";
export default function SignupPage(){
  const desktopBuild=process.env.NEXT_PUBLIC_DESKTOP_BUILD==="true";
  const demoMode=process.env.NEXT_PUBLIC_APP_MODE!=="live";
  const description=desktopBuild
    ? "Account creation opens in the hosted product; installed workspace records remain local."
    : demoMode
      ? "Create an optional hosted identity. Demonstration research, settings, and paper positions are not account-synced."
      : "Secure cloud sync for account-owned research settings and simulated positions.";
  return <AuthShell title="Create your workspace" description={description}><Suspense><AuthForm mode="signup"/></Suspense></AuthShell>;
}
