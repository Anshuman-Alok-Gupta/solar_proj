"use client";

import { Sun } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

import { ErrorState, LoadingBlock } from "@/components/state";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { endpoints } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { longDate } from "@/lib/format";
import { errorMessage, useApi } from "@/lib/use-api";

const STATUS_TEXT: Record<string, string> = {
  accepted: "This invite has already been used. Sign in instead.",
  revoked: "This invite was revoked by the plant owner. Ask them for a new link.",
  expired: "This invite has expired. Ask the plant owner for a new link.",
};

export function InviteClient() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const { loginWithToken } = useApp();
  const info = useApi(() => endpoints.invitation(token), [token]);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const existing = info.data?.account_exists ?? false;
  const mismatch = !existing && confirm.length > 0 && confirm !== password;

  async function accept(e: React.FormEvent) {
    e.preventDefault();
    if (mismatch) return;
    setBusy(true);
    setError(null);
    try {
      const res = await endpoints.acceptInvitation(token, password, existing ? undefined : name.trim() || undefined);
      await loginWithToken(res.access_token);
      router.replace("/");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/30 p-4">
      <Card className="w-full max-w-md">
        <CardHeader className="space-y-2">
          <div className="flex items-center gap-2">
            <span className="flex h-9 w-9 items-center justify-center rounded-md bg-amber-500 text-white">
              <Sun className="h-5 w-5" />
            </span>
            <CardTitle>You&apos;re invited</CardTitle>
          </div>
          {info.data && (
            <CardDescription>
              {info.data.invited_by ? `${info.data.invited_by} invited ` : "You were invited "}
              <strong>{info.data.email}</strong> to <strong>{info.data.plant_name}</strong> as {info.data.role === "viewer" ? "a viewer" : "an editor"}. Expires {longDate(info.data.expires_at.slice(0, 10))}.
            </CardDescription>
          )}
        </CardHeader>
        <CardContent>
          {info.loading && !info.data ? (
            <LoadingBlock lines={3} />
          ) : info.error ? (
            <ErrorState message={info.error === "Invitation not found" ? "This invite link is not valid. Check that you copied the whole link." : info.error} />
          ) : info.data && info.data.status !== "pending" ? (
            <div className="space-y-3 text-sm">
              <p>{STATUS_TEXT[info.data.status]}</p>
              <Button variant="outline" onClick={() => router.replace("/login")}>Go to sign in</Button>
            </div>
          ) : info.data ? (
            <form onSubmit={accept} className="space-y-4">
              {existing ? (
                <p className="text-sm text-muted-foreground">An account for this e-mail already exists. Enter its password to add this plant to it.</p>
              ) : (
                <div className="space-y-1.5">
                  <Label htmlFor="name">Your name (optional)</Label>
                  <Input id="name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
                </div>
              )}
              <div className="space-y-1.5">
                <Label htmlFor="password">{existing ? "Password" : "Choose a password"}</Label>
                <Input id="password" type="password" autoComplete={existing ? "current-password" : "new-password"} minLength={existing ? undefined : 8} required value={password} onChange={(e) => setPassword(e.target.value)} />
              </div>
              {!existing && (
                <div className="space-y-1.5">
                  <Label htmlFor="confirm">Confirm password</Label>
                  <Input id="confirm" type="password" autoComplete="new-password" minLength={8} required value={confirm} onChange={(e) => setConfirm(e.target.value)} aria-invalid={mismatch} />
                </div>
              )}
              {(error || mismatch) && (
                <p role="alert" className="text-sm text-destructive">
                  {mismatch ? "The passwords do not match." : error}
                </p>
              )}
              <Button type="submit" className="w-full" disabled={busy || mismatch}>
                {busy ? "Joining…" : existing ? "Add plant to my account" : "Create account & join"}
              </Button>
            </form>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
