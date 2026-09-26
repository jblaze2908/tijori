// Onboarding and settings endpoints. They aren't in docs/api.md yet: these are the shapes proposed to the
// backend (API_ASSUMPTIONS.md), and every read is optional, so a 404 renders "not available yet".
// Secrets (app passwords, statement passwords) are only ever sent, never read back.
import { invalidate, optional, prime, request } from "./api";
import type { AccountKind, ISODate } from "./types";

export const STEPS = ["profile", "accounts", "mail", "gmail", "passwords", "backfill"] as const;
export type Step = (typeof STEPS)[number];
export const STEP_LABEL: Record<Step, string> = {
  profile: "Profile",
  accounts: "Your accounts",
  mail: "Connect mail",
  gmail: "Set up the label",
  passwords: "Statement passwords",
  backfill: "Import history",
};

export interface Onboarding {
  step: Step | "done";
  completed: Step[];
  gmail_filter: string;
  label: string;
  gmail_check: GmailCheck | null;
  backfill: Backfill;
}
export interface GmailCheck {
  label_found: boolean;
  messages: number;
  checked_at: string;
}
export interface Backfill {
  status: "idle" | "running" | "done" | "error";
  processed: number;
  total: number | null;
  txns: number;
  message: string | null;
}

export interface ClassifyProfile {
  own_names: string[];
  own_vpas: string[];
  own_account_masks: string[];
  investment_account_masks: string[];
  employer_patterns: string[];
}

export type Provider = "gmail" | "outlook" | "yahoo" | "zoho" | "other";
export interface MailSource {
  id: number;
  provider: Provider;
  email: string;
  host: string;
  port: number;
  label: string;
  status: "active" | "paused" | "error";
  last_sync_at: string | null;
  last_error: string | null;
  messages_seen: number;
}
export interface MailDraft {
  provider: Provider;
  host: string;
  port: number;
  email: string;
  app_password: string;
}

export interface PasswordSlot {
  account_id: number;
  account_label: string;
  set: boolean;
  updated_at: string | null;
}

export interface Household {
  id: number;
  name: string;
  members: { id: number; name: string; email: string; role: string; joined_at: ISODate | null }[];
  invites: { id: number; email: string; status: "pending" | "accepted" | "expired"; created_at: string; expires_at: string }[];
}
export interface InviteInfo {
  valid: boolean;
  household_name: string | null;
  inviter_name: string | null;
  email: string | null;
  expires_at: string | null;
}

/** IMAP presets; "other" leaves host and port to the member. All use implicit TLS on 993. */
export const PROVIDERS: Record<Provider, { name: string; host: string; port: number }> = {
  gmail: { name: "Gmail", host: "imap.gmail.com", port: 993 },
  outlook: { name: "Outlook / Hotmail", host: "outlook.office365.com", port: 993 },
  yahoo: { name: "Yahoo Mail", host: "imap.mail.yahoo.com", port: 993 },
  zoho: { name: "Zoho Mail", host: "imap.zoho.in", port: 993 },
  other: { name: "Other (IMAP)", host: "", port: 993 },
};

const enc = encodeURIComponent;

export const setup = {
  onboarding: () => optional("/api/onboarding", (o: Onboarding) => o),
  classifyProfile: () => optional("/api/classify-profile", (p: ClassifyProfile) => p),
  mailSources: () => optional("/api/mail-sources", (r: { items: MailSource[] }) => r.items),
  passwords: () => optional("/api/statement-passwords", (r: { items: PasswordSlot[] }) => r.items),
  household: () => optional("/api/household", (h: Household) => h),
  invite: (token: string) => optional(`/api/invites/${enc(token)}`, (i: InviteInfo) => i),
};

// ---------- writes (each invalidates what it changes) ----------

/** The PATCH returns the new state; seeding the cache with it means finishing setup can't bounce back into the wizard. */
export async function saveStep(step: Step | "done") {
  const o = await request<Onboarding | undefined>("/api/onboarding", "PATCH", { step });
  if (o && typeof o === "object" && "step" in o) prime("/api/onboarding", o);
  else invalidate(["/api/onboarding"]);
}
export async function checkGmail(): Promise<GmailCheck> {
  const r = await request<GmailCheck>("/api/onboarding/check-gmail", "POST", {});
  invalidate(["/api/onboarding"]);
  return r;
}
export async function startBackfill(): Promise<Backfill> {
  const r = await request<Backfill>("/api/onboarding/backfill", "POST", {});
  invalidate(["/api/onboarding"]);
  return r;
}

export async function saveProfile(name: string, monthStartDay: number) {
  await Promise.all([request("/api/me", "PATCH", { name }), request("/api/settings", "PATCH", { month_start_day: monthStartDay })]);
  // Month maths everywhere depends on the start day, so every derived view refetches.
  invalidate(["/api/me", "/api/settings", "/api/months", "/api/summary", "/api/trends"]);
}

export async function saveClassifyProfile(p: ClassifyProfile) {
  await request("/api/classify-profile", "PATCH", p);
  invalidate(["/api/classify-profile"]);
}

export async function addAccount(a: { institution: string; name: string | null; kind: AccountKind; mask: string }) {
  await request("/api/accounts", "POST", a);
  invalidate(["/api/accounts", "/api/statement-passwords"]);
}
export async function removeAccount(id: number) {
  await request(`/api/accounts/${id}`, "DELETE");
  invalidate(["/api/accounts", "/api/statement-passwords"]);
}

/** Checks the credentials without storing anything; the server answers with a count or a friendly error. */
export const testMail = (d: MailDraft) => request<{ ok: boolean; messages?: number; error?: string }>("/api/mail-sources/test", "POST", d);
export async function addMailSource(d: MailDraft) {
  const r = await request<MailSource>("/api/mail-sources", "POST", d);
  invalidate(["/api/mail-sources", "/api/onboarding"]);
  return r;
}
export async function rotateMailPassword(id: number, app_password: string) {
  await request(`/api/mail-sources/${id}`, "PATCH", { app_password });
  invalidate(["/api/mail-sources"]);
}
export async function removeMailSource(id: number) {
  await request(`/api/mail-sources/${id}`, "DELETE");
  invalidate(["/api/mail-sources"]);
}

export async function setStatementPassword(accountId: number, password: string) {
  await request(`/api/statement-passwords/${accountId}`, "PUT", { password });
  invalidate(["/api/statement-passwords"]);
}
export async function clearStatementPassword(accountId: number) {
  await request(`/api/statement-passwords/${accountId}`, "DELETE");
  invalidate(["/api/statement-passwords"]);
}

export async function inviteMember(email: string) {
  const r = await request<{ id: number; email: string; url: string; expires_at: string }>("/api/household/invites", "POST", { email });
  invalidate(["/api/household"]);
  return r;
}
export async function revokeInvite(id: number) {
  await request(`/api/household/invites/${id}`, "DELETE");
  invalidate(["/api/household"]);
}

export async function signOut() {
  await request("/auth/logout", "POST", {});
  invalidate(["/api/"]);
}
