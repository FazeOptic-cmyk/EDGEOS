import { Suspense } from "react";
import { AuthForm } from "@/components/auth/auth-form";
import { AuthShell } from "@/components/auth/auth-shell";
export default function UpdatePasswordPage(){return <AuthShell title="Choose a new password" description="Use at least eight characters and avoid a reused password."><Suspense><AuthForm mode="update"/></Suspense></AuthShell>}
