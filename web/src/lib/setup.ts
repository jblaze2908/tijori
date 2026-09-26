// Onboarding, mail sources, secrets and invites (docs/api.md, "Onboarding" and "Google sign-in").
// Secrets (app passwords, statement passwords) are only ever sent, never read back.
import { ApiError, invalidate, optional, prime, request } from "./api";
import type { ISODate } from "./types";

/** Server steps (docs/api.md). The UI splits "mail" into connecting and the label setup; both save as "mail". */
export type ServerStep = "profile" | "mail" | "statement_passwords" | "first_upload" | "done";
export const STEPS = ["profile", "mail", "label", "statement_passwords", "first_upload"] as const;
export type Step = (typeof STEPS)[number];
export const STEP_LABEL: Record<Step, string> = {
  profile: "Profile",
  mail: "Connect mail",
  label: "Set up the label",
  statement_passwords: "Statement passwords",
  first_upload: "First statement",
};
export const serverStep = (s: Step): ServerStep => (s === "label" ? "mail" : s);

export interface Onboarding {
  step: ServerStep;
  completed_at: string | null;
  steps: ServerStep[];
  checklist: { profile: boolean; mail_source: boolean; statement_passwords: boolean; first_upload: boolean };
  /** Not in docs/api.md yet; requested so the label step can offer a copyable Gmail filter. */
  gmail_filter?: string | null;
}

export interface ClassifyProfile {
  own_names: string[];
  own_vpas: string[];
  own_account_masks: string[];
  investment_account_masks: string[];
  employer_patterns: string[];
}

export type Provider = "gmail" | "outlook" | "yahoo" | "custom";
export interface MailSource {
  id: number;
  provider: Provider;
  host: string;
  port: number;
  email: string;
  label: string;
  status: "untested" | "ok" | "error";
  last_tested_at: string | null;
  last_error_code: MailError | null;
  created_at: string;
}
export type MailError =
  | "auth_failed"
  | "mailbox_not_found"
  | "dns_failed"
  | "host_not_public"
  | "tls_failed"
  | "timeout"
  | "connect_refused"
  | "connect_failed"
  | "protocol_error";
export interface MailTest {
  ok: boolean;
  message_count: number | null;
  error_code: MailError | null;
}

export interface InviteInfo {
  email: string;
  household: string | { name: string } | null;
  expires_at: string;
  status: "valid" | "used" | "expired";
}

/** Presets the server applies itself; only "custom" sends host and port. */
export const PROVIDERS: Record<Provider, { name: string; host: string; port: number }> = {
  gmail: { name: "Gmail", host: "imap.gmail.com", port: 993 },
  outlook: { name: "Outlook / Hotmail", host: "outlook.office365.com", port: 993 },
  yahoo: { name: "Yahoo Mail", host: "imap.mail.yahoo.com", port: 993 },
  custom: { name: "Other (IMAP)", host: "", port: 993 },
};

/** docs/api.md error codes; the server never echoes banners or exception text, and neither does the UI. */
export const MAIL_ERROR: Record<MailError, string> = {
  auth_failed: "The email or app password was rejected. Create a new app password and paste it again.",
  mailbox_not_found: "Signed in, but there's no label with that name yet. Create it (next step), then check again.",
  dns_failed: "That server name couldn't be found. Check the IMAP server.",
  host_not_public: "That server isn't on the public internet, so Tijori won't connect to it.",
  tls_failed: "The secure connection to the server failed.",
  timeout: "The server didn't answer in time. Try again in a minute.",
  connect_refused: "The server refused the connection. Check the port.",
  connect_failed: "Couldn't connect to the server.",
  protocol_error: "The server answered in a way Tijori didn't expect.",
};

/** docs/api.md auth_error values, from /?auth_error=<reason> after a failed Google sign-in. */
export const AUTH_ERROR: Record<string, string> = {
  cancelled: "Sign-in was cancelled.",
  bad_request: "Google didn't accept the sign-in request. Try again.",
  state_invalid: "That sign-in expired or was opened in another browser. Start again here.",
  exchange_failed: "Google didn't confirm the sign-in. Try again.",
  token_invalid: "Google's answer couldn't be verified. Try again.",
  token_expired: "Google's answer expired before it arrived. Try again.",
  email_unverified: "Your Google email isn't verified yet. Verify it with Google, then sign in again.",
  not_invited: "This Google account isn't part of a Tijori household. Ask your household admin for an invite link.",
};

const enc = encodeURIComponent;

export const setup = {
  onboarding: () => optional("/api/onboarding", (o: Onboarding) => o),
  classifyProfile: () => optional("/api/profile/classify", (p: ClassifyProfile) => p),
  mailSources: () => optional("/api/mail-sources", (r: { items: MailSource[] }) => r.items),
  invite: (token: string) => optional(`/api/invites/${enc(token)}`, (i: InviteInfo) => i),
};

// ---------- writes (each invalidates what it changes) ----------

/** The PATCH returns the new state; seeding the cache with it means finishing setup can't bounce back into the wizard. */
async function patchOnboarding(body: { step?: ServerStep; completed?: boolean }) {
  const o = await request<Onboarding | undefined>("/api/onboarding", "PATCH", body);
  if (o && typeof o === "object" && "step" in o) prime("/api/onboarding", o);
  else invalidate(["/api/onboarding"]);
}
export const saveStep = (s: Step) => patchOnboarding({ step: serverStep(s) });
export const finishOnboarding = () => patchOnboarding({ completed: true });

export async function saveMonthStart(month_start_day: number) {
  await request("/api/settings", "PATCH", { month_start_day });
  // Month maths everywhere depends on the start day, so every derived view refetches.
  invalidate(["/api/me", "/api/settings", "/api/months", "/api/summary", "/api/trends", "/api/onboarding"]);
}

export async function saveClassifyProfile(p: ClassifyProfile) {
  await request("/api/profile/classify", "PUT", p);
  invalidate(["/api/profile/classify", "/api/onboarding"]);
}

export async function addMailSource(d: { provider: Provider; host: string; port: number; email: string; app_password: string; label: string }) {
  const body = d.provider === "custom" ? d : { provider: d.provider, email: d.email, app_password: d.app_password, label: d.label };
  const r = await request<MailSource>("/api/mail-sources", "POST", body);
  invalidate(["/api/mail-sources", "/api/onboarding"]);
  return r;
}
/** Rate-limited server-side to 5 per 10 minutes; a 429 surfaces as the client's own "too many attempts" message. */
export async function testMailSource(id: number): Promise<MailTest> {
  const r = await request<MailTest>(`/api/mail-sources/${id}/test`, "POST", {});
  invalidate(["/api/mail-sources"]);
  return r;
}
export async function rotateMailPassword(id: number, app_password: string) {
  await request(`/api/mail-sources/${id}`, "PATCH", { app_password });
  invalidate(["/api/mail-sources"]);
}
export async function removeMailSource(id: number) {
  await request(`/api/mail-sources/${id}`, "DELETE");
  invalidate(["/api/mail-sources", "/api/onboarding"]);
}

export async function setStatementPassword(accountId: number, password: string) {
  await request(`/api/accounts/${accountId}/statement-password`, "PUT", { password });
  invalidate(["/api/accounts", "/api/onboarding"]);
}
export async function clearStatementPassword(accountId: number) {
  await request(`/api/accounts/${accountId}/statement-password`, "DELETE");
  invalidate(["/api/accounts", "/api/onboarding"]);
}

/** Admin only (403 otherwise); 422 when the email is already a member. The token/url is shown once. */
export async function createInvite(email: string) {
  try {
    return await request<{ email: string; token: string; url: string; expires_at: string }>("/api/invites", "POST", { email });
  } catch (e) {
    if (e instanceof ApiError && e.status === 422) throw new ApiError(422, "That email is already a member, or isn't a valid address.");
    if (e instanceof ApiError && e.status === 403) throw new ApiError(403, "Only the household admin can invite people.");
    throw e;
  }
}

export async function signOut() {
  await request("/auth/logout", "POST", {});
  invalidate(["/api/"]);
}

// ---------- first statement upload (multipart, docs/api.md POST /api/uploads) ----------

export interface UploadResult {
  duplicate: boolean;
  institution?: string;
  account?: { label: string } | null;
  period_start?: ISODate;
  period_end?: ISODate;
  reconciliation?: { ok: boolean; closing_diff?: string } | null;
  txns?: { lines: number; created: number; matched_existing: number; filed: number; inbox: number };
}

/**
 * The PDF password reaches the server in the form body only, is never stored by the UI, and the caller clears its
 * field afterwards. A 422 is mapped from its parse_status enum; the server's detail text is never shown.
 */
export async function uploadStatement(file: File, password: string): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  if (password) form.append("password", password);
  let res: Response;
  try {
    res = await fetch("/api/uploads", { method: "POST", body: form, credentials: "same-origin", headers: { "X-Requested-With": "tijori", Accept: "application/json" } });
  } catch {
    throw new ApiError(0, "Can't reach the Tijori server.");
  }
  if (res.status === 422) {
    const body = (await res.json().catch(() => ({}))) as { parse_status?: string };
    throw new ApiError(
      422,
      body.parse_status === "parser_needed"
        ? "Tijori can't read statements from this bank yet. The file is kept, so it's read once a parser exists."
        : "The statement couldn't be read. If it's a locked PDF, add its password and try again.",
    );
  }
  if (res.status === 415) throw new ApiError(415, "That file isn't a PDF or a text statement.");
  if (res.status === 413) throw new ApiError(413, "That file is too large.");
  if (!res.ok) throw new ApiError(res.status, res.status === 401 ? "You're signed out." : `The upload failed (${res.status}).`);
  invalidate(["/api/"]);
  return (await res.json()) as UploadResult;
}
