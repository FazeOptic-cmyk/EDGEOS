import { Suspense } from "react";
import { AuthForm } from "@/components/auth/auth-form";
import { AuthShell } from "@/components/auth/auth-shell";
export default function ForgotPasswordPage(){return <AuthShell title="Reset your password" description="We’ll send a secure recovery link to your verified email."><Suspense><AuthForm mode="forgot"/></Suspense></AuthShell>}
