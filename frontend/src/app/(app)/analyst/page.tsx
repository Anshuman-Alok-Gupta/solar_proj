"use client";

import { Bot, Send, Trash2, Wrench } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { EmptyState, InlineSpinner, PageHeader } from "@/components/state";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { endpoints } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { ago } from "@/lib/format";
import { errorMessage, useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";

interface Msg {
  id: string;
  role: "user" | "assistant";
  content: string;
  tool_calls?: { tool: string; input: Record<string, unknown>; ok: boolean; error?: string }[] | null;
}

/** Minimal markdown: paragraphs, bullet lists, tables and bold. Good enough for analyst replies. */
function Markdown({ text }: { text: string }) {
  const blocks = text.split(/\n{2,}/);
  return (
    <div className="space-y-2 text-sm leading-relaxed">
      {blocks.map((b, i) => {
        const lines = b.split("\n");
        if (lines.every((l) => l.trim().startsWith("|")) && lines.length >= 2) {
          const rows = lines.filter((l) => !/^\s*\|?\s*:?-{2,}/.test(l)).map((l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
          return (
            <div key={i} className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead><tr>{rows[0].map((c, j) => <th key={j} className="border-b px-2 py-1 text-left font-medium">{inline(c)}</th>)}</tr></thead>
                <tbody>{rows.slice(1).map((r, k) => <tr key={k}>{r.map((c, j) => <td key={j} className="border-b px-2 py-1 tabular-nums">{inline(c)}</td>)}</tr>)}</tbody>
              </table>
            </div>
          );
        }
        if (lines.every((l) => /^\s*([-*•]|\d+\.)\s/.test(l))) {
          return <ul key={i} className="list-disc space-y-1 pl-5">{lines.map((l, j) => <li key={j}>{inline(l.replace(/^\s*([-*•]|\d+\.)\s/, ""))}</li>)}</ul>;
        }
        if (/^#{1,3}\s/.test(lines[0])) return <div key={i} className="font-semibold">{inline(lines[0].replace(/^#+\s/, ""))}</div>;
        return <p key={i}>{lines.map((l, j) => <span key={j}>{inline(l)}{j < lines.length - 1 && <br />}</span>)}</p>;
      })}
    </div>
  );
}

function inline(s: string): React.ReactNode {
  const parts = s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((p, i) => (p.startsWith("**") ? <strong key={i}>{p.slice(2, -2)}</strong> : p.startsWith("`") ? <code key={i} className="rounded bg-muted px-1 text-xs">{p.slice(1, -1)}</code> : <span key={i}>{p}</span>));
}

export default function AnalystPage() {
  const { plant } = useApp();
  const pid = plant?.id ?? null;
  const status = useApi(pid ? () => endpoints.aiStatus(pid) : null, [pid]);
  const convs = useApi(pid ? () => endpoints.aiConversations(pid) : null, [pid]);
  const [cid, setCid] = useState<number | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  async function loadConversation(id: number) {
    if (!pid) return;
    try {
      const c = await endpoints.aiConversation(pid, id);
      setCid(id);
      setMessages(c.messages.map((m) => ({ id: String(m.id), role: m.role as Msg["role"], content: m.content, tool_calls: m.tool_calls })));
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  async function send(text: string) {
    if (!pid || !text.trim() || busy) return;
    setInput("");
    setMessages((m) => [...m, { id: `u-${Date.now()}`, role: "user", content: text }]);
    setBusy(true);
    try {
      const r = await endpoints.aiChat(pid, text, cid);
      setCid(r.conversation_id);
      setMessages((m) => [...m, { id: `a-${r.conversation_id}-${Date.now()}`, role: "assistant", content: r.reply, tool_calls: r.tool_calls }]);
      void convs.refetch();
    } catch (e) {
      setMessages((m) => [...m, { id: `e-${Date.now()}`, role: "assistant", content: `⚠️ ${errorMessage(e)}` }]);
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: number) {
    if (!pid) return;
    await endpoints.aiDelete(pid, id);
    if (cid === id) {
      setCid(null);
      setMessages([]);
    }
    void convs.refetch();
  }

  if (!plant) return <EmptyState title="No plant selected" />;

  return (
    <div className="flex h-[calc(100vh-7.5rem)] flex-col">
      <PageHeader title="AI Energy Analyst" description="Answers are grounded in your plant's data through backend tools; every reply shows which tools were used." actions={<Button variant="outline" size="sm" onClick={() => { setCid(null); setMessages([]); }}>New conversation</Button>} />

      {status.data && !status.data.configured && (
        <Alert className="mb-4">
          <AlertTitle>AI analyst not configured</AlertTitle>
          <AlertDescription>{status.data.setup_hint}</AlertDescription>
        </Alert>
      )}

      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-4">
        <Card className="hidden min-h-0 lg:flex lg:flex-col">
          <CardContent className="min-h-0 flex-1 p-2">
            <div className="px-2 py-1 text-xs font-medium text-muted-foreground">Conversations</div>
            <ScrollArea className="h-full">
              {convs.data?.items.length ? (
                <ul className="space-y-1">
                  {convs.data.items.map((c) => (
                    <li key={c.id} className={cn("group flex items-center gap-1 rounded-md px-2 py-1.5 text-sm hover:bg-muted", cid === c.id && "bg-muted")}>
                      <button type="button" className="min-w-0 flex-1 truncate text-left" onClick={() => loadConversation(c.id)} title={c.title ?? ""}>{c.title ?? "Untitled"}<span className="block text-[11px] text-muted-foreground">{ago(c.updated_at)} · {c.message_count} msgs</span></button>
                      <Button variant="ghost" size="icon" className="h-6 w-6 opacity-0 group-hover:opacity-100" aria-label="Delete" onClick={() => remove(c.id)}><Trash2 className="h-3.5 w-3.5" /></Button>
                    </li>
                  ))}
                </ul>
              ) : <p className="px-2 text-xs text-muted-foreground">No conversations yet.</p>}
            </ScrollArea>
          </CardContent>
        </Card>

        <Card className="flex min-h-0 flex-col lg:col-span-3">
          <CardContent className="flex min-h-0 flex-1 flex-col p-0">
            <ScrollArea className="min-h-0 flex-1 p-4">
              {messages.length === 0 ? (
                <div className="flex h-full flex-col items-center justify-center gap-4 py-10 text-center">
                  <Bot className="h-10 w-10 text-muted-foreground" />
                  <p className="max-w-md text-sm text-muted-foreground">Ask about generation, forecasts, anomalies, weather effects or savings. The analyst only uses measured data and labelled estimates.</p>
                  <div className="flex max-w-2xl flex-wrap justify-center gap-2">
                    {(status.data?.suggested_questions ?? []).map((q) => (
                      <Button key={q} variant="outline" size="sm" onClick={() => send(q)} disabled={busy || !status.data?.configured}>{q}</Button>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="space-y-4">
                  {messages.map((m) => (
                    <div key={m.id} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
                      <div className={cn("max-w-[85%] rounded-lg px-4 py-3", m.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted")}>
                        {m.role === "user" ? <p className="whitespace-pre-wrap text-sm">{m.content}</p> : <Markdown text={m.content} />}
                        {m.tool_calls && m.tool_calls.length > 0 && (
                          <div className="mt-2 flex flex-wrap gap-1 border-t pt-2">
                            {m.tool_calls.map((t, i) => (
                              <Badge key={i} variant={t.ok ? "outline" : "destructive"} className="gap-1 font-normal" title={JSON.stringify(t.input)}><Wrench className="h-3 w-3" /> {t.tool}</Badge>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                  {busy && <div className="flex justify-start"><div className="rounded-lg bg-muted px-4 py-3"><InlineSpinner label="Analysing your data…" /></div></div>}
                  <div ref={bottom} />
                </div>
              )}
            </ScrollArea>
            <form
              className="flex items-end gap-2 border-t p-3"
              onSubmit={(e) => {
                e.preventDefault();
                void send(input);
              }}
            >
              <Textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send(input);
                  }
                }}
                placeholder={status.data?.configured ? "Ask about your solar system… (Enter to send, Shift+Enter for a new line)" : "Configure an LLM provider in .env to enable the analyst"}
                disabled={busy || !status.data?.configured}
                rows={2}
                className="min-h-[44px] resize-none"
                aria-label="Message"
              />
              <Button type="submit" disabled={busy || !input.trim() || !status.data?.configured} aria-label="Send"><Send className="h-4 w-4" /></Button>
            </form>
          </CardContent>
        </Card>
      </div>
      {status.data?.configured && <p className="mt-2 text-xs text-muted-foreground">Provider: {status.data.provider} · model: {status.data.model} · tool mode: {status.data.tool_mode}</p>}
    </div>
  );
}
