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
  token?: string | null;
}
const prefs = () => resource("/api/settings", (r: Prefs) => r);
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
    </>
  );
}

/** Bearer tokens for Claude's MCP connector; a token is shown once. */
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
        Connect Claude to <span className="mono-n">{url}</span> with a bearer token. Claude can read summaries, transactions, subscriptions, net worth,
        alerts and the Inbox, and file a transaction under a category. UPI handles of people are masked in what it sees.
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
                    toast("Token revoked");
                  } catch {
                    toast("Couldn't revoke.");
                  }
                }}
              >
                Revoke
              </button>
            )}
          </div>
        ))}
      </div>
    </>
  );
}

