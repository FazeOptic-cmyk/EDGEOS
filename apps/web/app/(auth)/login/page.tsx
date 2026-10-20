import { Suspense } from "react";
import { AuthForm } from "@/components/auth/auth-form";
import { AuthShell } from "@/components/auth/auth-shell";
export default function LoginPage(){
  const desktopBuild=process.env.NEXT_PUBLIC_DESKTOP_BUILD==="true";
  const demoMode=process.env.NEXT_PUBLIC_APP_MODE!=="live";
  const description=desktopBuild
    ? "Sign in to an optional cloud account. Local watchlists, paper positions, and settings remain in this installed workspace."
    : demoMode
      ? "Sign in to an optional hosted identity. This demonstration workspace and its paper records are not account-synced."
      : "Sign in to sync account-owned research, watchlists, and paper portfolio data.";
  return <AuthShell title="Welcome back" description={description}><Suspense><AuthForm mode="login"/></Suspense></AuthShell>;
}
