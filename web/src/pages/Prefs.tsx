import { useState } from "react";
import { useToast } from "../components/Toast";
import { dataOf, invalidate, read, request, resource } from "../lib/api";

interface Prefs {
  raw_retention_days: number;
  notify_topic: string | null;
  notify_enabled: boolean;
}
interface Token {
  id: number;
  name: string;
  created_at: string;
  last_used_at: string | null;
  revoked: boolean;
  kind: "token" | "app";
  can_write: boolean;
  token?: string | null;
}
const prefs = () => resource("/api/settings", (r: Prefs) => r, "prefs");
const tokens = () => resource("/api/mcp/tokens", (r: { items: Token[] }) => r.items);
const RETENTION: [number, string][] = [
  [90, "90 days"],
  [180, "180 days"],
  [365, "1 year"],
  [730, "2 years"],
  [0, "Keep forever"],
];

/** Notifications (self-hosted ntfy) and how long original emails and PDFs are kept. */
export function NotifyRetention() {
  const p = dataOf(read(prefs()));
  const toast = useToast();
  const [topic, setTopic] = useState<string | null>(null);
  const save = async (body: Partial<Prefs>, done: string) => {
    try {
      await request("/api/settings", "PATCH", body);
      invalidate(["/api/settings", "/api/me"]);
      toast(done);
    } catch {
      toast("Couldn't save that. Check the topic: 12–64 letters, digits, - or _.");
    }
  };
  if (!p) return <p className="sub">Loading…</p>;
  const t = topic ?? p.notify_topic ?? "";
  return (
    <>
      <p className="sub">
        Pushes go to your ntfy topic: new alerts (duplicate charge, missed or pricier subscription, bounce risk, budget over or ahead of pace) once
        each, and a Monday 9:00 digest of last week's figures. Anyone who knows the topic can read it, so make it long.
      </p>
      <div className="row mt" style={{ gap: 8, flexWrap: "wrap" }}>
        <input className="inp2" style={{ minWidth: 260 }} placeholder="ntfy topic, e.g. tijori-7f3k2p9q4w" value={t} maxLength={64} onChange={(e) => setTopic(e.target.value)} aria-label="ntfy topic" />
        <button type="button" className="btn2 sm" disabled={t === (p.notify_topic ?? "")} onClick={() => save({ notify_topic: t || null }, t ? "Topic saved" : "Topic removed")}>
          Save topic
        </button>
        <label className="row" style={{ gap: 6 }}>
          <input type="checkbox" checked={p.notify_enabled} disabled={!p.notify_topic} onChange={(e) => save({ notify_enabled: e.target.checked }, e.target.checked ? "Notifications on" : "Notifications off")} />
          Send notifications
        </label>
        <button
          type="button"
          className="btn2 sm"
          disabled={!p.notify_topic}
          onClick={async () => {
            try {
              const r = await request<{ sent: boolean }>("/api/notify/test", "POST", {});
              toast(r.sent ? "Test sent" : "ntfy isn't configured on the server");
            } catch {
              toast("Couldn't send the test.");
            }
          }}
        >
          Send a test
        </button>
      </div>
      <h3 className="mt" style={{ fontSize: 14, margin: "24px 0 4px" }}>
        Keep original emails and PDFs
      </h3>
      <p className="sub">
        After this, the stored email and PDF files are deleted. Transactions, statement figures and sightings stay. Mail still waiting for a parser or a
        password is kept twice as long.
      </p>
      <select className="inp2" value={p.raw_retention_days} onChange={(e) => save({ raw_retention_days: Number(e.target.value) }, "Retention saved")} aria-label="Retention">
        {RETENTION.map(([d, l]) => (
          <option key={d} value={d}>
            {l}
          </option>
        ))}
      </select>
      <Backup />
    </>
  );
}

interface BackupState {
  configured: boolean;
  last_run_at: string | null;
  last_ok: boolean;
  last_detail: string | null;
  last_ok_at: string | null;
  stale: boolean;
}
const backupOf = () => resource("/api/ops/backup", (r: BackupState) => r);
const stamp = (iso: string) => new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" });

/** The nightly off-site backup's last report (deploy/backup.sh writes it). */
function Backup() {
  const b = dataOf(read(backupOf()));
  if (!b) return null;
  return (
    <>
      <h3 className="mt" style={{ margin: "20px 0 4px", fontSize: 14 }}>
        Off-site backup
      </h3>
      <p className={`sub${b.stale ? " bad-t" : ""}`}>
        {!b.configured
          ? "No backup has reported yet."
          : b.last_ok_at
            ? `Last good backup ${stamp(b.last_ok_at)} · ${b.last_ok ? b.last_detail ?? "" : `last run failed: ${b.last_detail ?? ""}`}`
            : `No good backup yet · last run failed: ${b.last_detail ?? ""}`}
      </p>
    </>
  );
}

/** MCP access: apps connected by signing in, and bearer tokens (shown once) for clients that take a header. */
export function McpTokens() {
  const st = read(tokens());
  const toast = useToast();
  const [name, setName] = useState("Claude");
  const [fresh, setFresh] = useState<string | null>(null);
  const items = dataOf(st) ?? [];
  const url = `${location.origin}/mcp`;
  return (
    <>
      <p className="sub">
        Add <span className="mono-n">{url}</span> as a connector in any MCP client (Claude, ChatGPT, Cursor and others) and sign in when it asks. You choose
        read-only or read and write. A client that takes a header instead can use a bearer token from below. UPI handles of people are masked in what it
        sees.
      </p>
      <div className="row mt" style={{ gap: 8 }}>
        <input className="inp2" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} aria-label="Token name" />
        <button
          type="button"
          className="btn2 sm primary"
          disabled={!name.trim()}
          onClick={async () => {
            try {
              const r = await request<Token>("/api/mcp/tokens", "POST", { name: name.trim() });
              setFresh(r.token ?? null);
              invalidate(["/api/mcp/tokens"]);
            } catch {
              toast("Couldn't create a token.");
            }
          }}
        >
          Create token
        </button>
      </div>
      {fresh && (
        <div className="rawbox mt">
          <small className="faint">Copy it now; it isn't shown again.</small>
          <pre>{fresh}</pre>
        </div>
      )}
      <div className="rows mt">
        {items.map((t) => (
          <div key={t.id} className="lrow2">
            <span className="grow">
              {t.name}
              <small className="faint">
                {" "}
                · {t.kind === "app" ? `connected app, ${t.can_write ? "read and write" : "read only"}` : "token"}
                · created {t.created_at.slice(0, 10)}
                {t.last_used_at ? ` · used ${t.last_used_at.slice(0, 10)}` : " · never used"}
                {t.revoked ? " · revoked" : ""}
              </small>
            </span>
            {!t.revoked && (
              <button
                type="button"
                className="btn2 sm"
                onClick={async () => {
                  try {
                    await request(`/api/mcp/tokens/${t.id}/revoke`, "POST", {});
                    invalidate(["/api/mcp/tokens"]);
                    toast(t.kind === "app" ? "Disconnected" : "Token revoked");
                  } catch {
                    toast("Couldn't revoke.");
                  }
                }}
              >
                {t.kind === "app" ? "Disconnect" : "Revoke"}
              </button>
            )}
          </div>
        ))}
      </div>
    </>
  );
}

