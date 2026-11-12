"use client";

import { Copy, Trash2, UserPlus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ErrorState, LoadingBlock } from "@/components/state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { type Member, type Plant, endpoints } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { errorMessage, useApi } from "@/lib/use-api";

const ROLE_HELP: Record<Member["role"], string> = {
  viewer: "Viewer: sees everything and can ask the AI analyst; cannot change data or settings.",
  editor: "Editor: can also sync, import, train models and edit settings; cannot share or delete the plant.",
};

export function inviteLink(token: string, origin: string): string {
  return `${origin.replace(/\/$/, "")}/invite/${encodeURIComponent(token)}`;
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success("Invite link copied");
  } catch {
    toast.message("Copy the link manually", { description: text });
  }
}

function RoleSelect({ value, onChange, id }: { value: Member["role"]; onChange: (r: Member["role"]) => void; id?: string }) {
  return (
    <Select value={value} onValueChange={(v) => v && onChange(v as Member["role"])}>
      <SelectTrigger id={id} className="w-[120px]" aria-label="Role">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="viewer">Viewer</SelectItem>
        <SelectItem value="editor">Editor</SelectItem>
      </SelectContent>
    </Select>
  );
}

export function SharingCard({ plant }: { plant: Plant }) {
  const members = useApi(() => endpoints.members(plant.id), [plant.id]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Member["role"]>("viewer");
  const [busy, setBusy] = useState(false);
  const [lastLink, setLastLink] = useState<string | null>(null);

  async function invite(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const inv = await endpoints.invite(plant.id, email.trim(), role);
      const link = inviteLink(inv.token ?? "", window.location.origin);
      setLastLink(link);
      setEmail("");
      await copy(link);
      await members.refetch();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function run(fn: () => Promise<unknown>, ok: string) {
    try {
      await fn();
      toast.success(ok);
      await members.refetch();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Sharing</CardTitle>
        <CardDescription>Invite family members to {plant.name}. Each invite is a one-time link valid for 7 days; send it however you like (no e-mail is sent).</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <form onSubmit={invite} className="flex flex-wrap items-end gap-2">
          <div className="min-w-[200px] flex-1 space-y-1.5">
            <Label htmlFor="invite-email">E-mail</Label>
            <Input id="invite-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com" />
          </div>
          <RoleSelect id="invite-role" value={role} onChange={setRole} />
          <Button type="submit" disabled={busy}>
            <UserPlus className="h-4 w-4" /> {busy ? "Creating…" : "Create invite"}
          </Button>
        </form>
        <p className="text-xs text-muted-foreground">{ROLE_HELP[role]}</p>
        {lastLink && (
          <div className="flex items-center gap-2 rounded-md border bg-muted/40 p-2 text-xs">
            <code className="min-w-0 flex-1 truncate" title={lastLink}>{lastLink}</code>
            <Button size="sm" variant="ghost" onClick={() => copy(lastLink)} aria-label="Copy invite link">
              <Copy className="h-3.5 w-3.5" />
            </Button>
          </div>
        )}

        {members.loading && !members.data ? (
          <LoadingBlock lines={2} />
        ) : members.error ? (
          <ErrorState message={members.error} onRetry={members.refetch} />
        ) : members.data ? (
          <div className="space-y-4 text-sm">
            <div>
              <div className="mb-1 font-medium">People with access</div>
              <ul className="divide-y rounded-md border">
                {members.data.owner && (
                  <li className="flex items-center gap-2 px-3 py-2">
                    <span className="min-w-0 flex-1 truncate">{members.data.owner.full_name ?? members.data.owner.email}</span>
                    <Badge variant="secondary">Owner</Badge>
                  </li>
                )}
                {members.data.members.map((m) => (
                  <li key={m.user_id} className="flex flex-wrap items-center gap-2 px-3 py-2">
                    <span className="min-w-0 flex-1 truncate" title={m.email}>{m.full_name ? `${m.full_name} · ${m.email}` : m.email}</span>
                    <RoleSelect value={m.role} onChange={(r) => run(() => endpoints.setMemberRole(plant.id, m.user_id, r), "Role updated")} />
                    <Button size="icon" variant="ghost" aria-label={`Remove ${m.email}`} onClick={() => run(() => endpoints.removeMember(plant.id, m.user_id), "Access removed")}>
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </li>
                ))}
                {members.data.members.length === 0 && <li className="px-3 py-2 text-muted-foreground">Nobody else yet.</li>}
              </ul>
            </div>
            {members.data.invitations.length > 0 && (
              <div>
                <div className="mb-1 font-medium">Pending invites</div>
                <ul className="divide-y rounded-md border">
                  {members.data.invitations.map((i) => (
                    <li key={i.id} className="flex flex-wrap items-center gap-2 px-3 py-2">
                      <span className="min-w-0 flex-1 truncate">{i.email}</span>
                      <Badge variant="outline" className="capitalize">{i.role}</Badge>
                      <span className="text-xs text-muted-foreground">expires {shortDate(i.expires_at)}</span>
                      <Button size="sm" variant="ghost" onClick={() => run(() => endpoints.revokeInvite(plant.id, i.id), "Invite revoked")}>
                        Revoke
                      </Button>
                    </li>
                  ))}
                </ul>
                <p className="mt-1 text-xs text-muted-foreground">Links are shown only once. Lost one? Revoke it and create a new invite.</p>
              </div>
            )}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function AccountCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const mismatch = confirm.length > 0 && confirm !== next;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (next !== confirm) return;
    setBusy(true);
    try {
      await endpoints.changePassword(current, next);
      setCurrent("");
      setNext("");
      setConfirm("");
      toast.success("Password changed");
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Your account</CardTitle>
        <CardDescription>Change your sign-in password (at least 8 characters).</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="pw-current">Current password</Label>
            <Input id="pw-current" type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="pw-new">New password</Label>
            <Input id="pw-new" type="password" autoComplete="new-password" minLength={8} required value={next} onChange={(e) => setNext(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="pw-confirm">Confirm new password</Label>
            <Input id="pw-confirm" type="password" autoComplete="new-password" minLength={8} required value={confirm} onChange={(e) => setConfirm(e.target.value)} aria-invalid={mismatch} />
          </div>
          {mismatch && <p role="alert" className="text-xs text-destructive sm:col-span-2">The new passwords do not match.</p>}
          <div className="sm:col-span-2">
            <Button type="submit" disabled={busy || mismatch}>{busy ? "Saving…" : "Change password"}</Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
