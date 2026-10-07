import { useEffect, useState, type ReactNode } from "react";
import { G, Keyhole } from "./components/Glyphs";
import { Empty, PageBoundary } from "./components/ui";
import { AppProvider, buildCtx, type AppCtx } from "./ctx";
import { api, dataOf, read, retryFailed } from "./lib/api";
import { initials, monthYear, plural, todayIST } from "./lib/format";
import { Link, match, navigate, useLocation } from "./lib/router";
import { setup } from "./lib/setup";
import { useStore } from "./lib/useStore";
import type { MonthKey } from "./lib/types";
import { Inbox } from "./pages/Inbox";
import { NetWorth } from "./pages/NetWorth";
import { Invite } from "./pages/Invite";
import { Onboarding } from "./pages/Onboarding";
import { Orders } from "./pages/Orders";
import { Overview } from "./pages/Overview";
import { SECTIONS, Settings, type Section } from "./pages/Settings";
import { Spending } from "./pages/Spending";
import { SpendingDetail } from "./pages/SpendingDetail";
import { Subscriptions } from "./pages/Subscriptions";
import { Transactions } from "./pages/Transactions";
import { Welcome } from "./pages/Welcome";

type RouteKey = "overview" | "spending" | "subscriptions" | "transactions" | "orders" | "networth" | "inbox";
/** `tab` is the phone tab-bar label: six tabs share 390px, so a route without one stays in the sidebar. */
const ROUTES: { path: string; key: RouteKey; title: string; tab?: string }[] = [
  { path: "/", key: "overview", title: "Overview", tab: "Overview" },
  { path: "/spending", key: "spending", title: "Spending", tab: "Spending" },
  { path: "/subscriptions", key: "subscriptions", title: "Subscriptions", tab: "Subs" },
  { path: "/transactions", key: "transactions", title: "Transactions", tab: "Activity" },
  { path: "/orders", key: "orders", title: "Orders" },
  { path: "/networth", key: "networth", title: "Net worth", tab: "Worth" },
  { path: "/inbox", key: "inbox", title: "Inbox", tab: "Inbox" },
];
/** Old paths from before UI v1, kept so bookmarks still land. */
const MOVED: Record<string, string> = { "/activity": "/transactions", "/trends": "/spending", "/recurring": "/subscriptions" };
/** Pages reachable while signed out; everything else sends a 401 on /api/me to /welcome. */
const PUBLIC = /^\/(welcome|invite\/[^/]+)$/;

const MONTH_KEY = "tj.month";

function readStoredMonth(): MonthKey | null {
  try {
    return localStorage.getItem(MONTH_KEY);
  } catch {
    return null;
  }
}

export function App() {
  useStore();
  const { path, params } = useLocation();
  const detail = match("/spending/:group/:key", path);
  const detailGroup = (["category", "merchant", "account"] as const).find((g) => g === detail?.group);
  const route = ROUTES.find((r) => r.path === (detailGroup ? "/spending" : path));
  const [picked, setPicked] = useState<MonthKey | null>(readStoredMonth);
  const pick = (m: MonthKey) => {
    setPicked(m);
    try {
      localStorage.setItem(MONTH_KEY, m);
    } catch {
      /* private mode: the choice just isn't remembered */
    }
  };
  const isPublic = PUBLIC.test(path);
  const me = read(api.me());
  const signedOut = me.status === "error" && me.error.status === 401;
  const onboarding = isPublic ? null : dataOf(read(setup.onboarding()));
  const setupPending = !!onboarding && !onboarding.completed_at && onboarding.step !== "done";
  const settingsMatch = match("/settings/:section", path);
  const section = SECTIONS.find(([k]) => k === settingsMatch?.section)?.[0] as Section | undefined;

  // A failed Google callback lands on /?auth_error=<reason>; the reason travels to /welcome, which explains it.
  useEffect(() => {
    if (!signedOut || isPublic) return;
    const here = new URLSearchParams(location.search);
    const authError = here.get("auth_error");
    here.delete("auth_error");
    const rest = here.toString();
    const q = new URLSearchParams({ next: location.pathname + (rest ? `?${rest}` : ""), ...(authError ? { auth_error: authError } : {}) });
    navigate(`/welcome?${q}`, { replace: true });
  }, [signedOut, isPublic]);

  // Landing on the dashboard with setup unfinished resumes the wizard; other pages just show a banner.
  useEffect(() => {
    if (path === "/" && setupPending) navigate("/onboarding", { replace: true });
    if (path === "/settings") navigate("/settings/general", { replace: true });
    if (MOVED[path]) navigate(MOVED[path] + location.search, { replace: true });
  }, [path, setupPending]);

  useEffect(() => {
    retryFailed();
    window.scrollTo(0, 0);
    if (path.startsWith("/onboarding")) return;
    document.title = route ? `${route.title} · Tijori` : section ? "Settings · Tijori" : path === "/welcome" ? "Welcome · Tijori" : "Tijori";
  }, [path, route, section]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        openSearch();
      }
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, []);

  if (path === "/welcome") return <Welcome next={params.get("next")} error={params.get("auth_error")} />;
  const invite = match("/invite/:token", path);
  if (invite) return <Invite token={invite.token!} />;
  if (signedOut) return null;
  const step = path === "/onboarding" ? { step: "" } : match("/onboarding/:step", path);
  if (step) return <Onboarding step={step.step ?? ""} />;

  const app = buildCtx(todayIST(), picked, pick);
  let page: ReactNode;
  switch (route?.key) {
    case "overview":
      page = (
        <Overview
          app={app}
          head={
            <div className="ph">
              <h1>Overview</h1>
              <div className="sp" />
              <MonthSwitcher app={app} />
            </div>
          }
        />
      );
      break;
    case "spending":
      page = detailGroup ? <SpendingDetail app={app} group={detailGroup} name={detail!.key!} /> : <Spending app={app} />;
      break;
    case "subscriptions":
      page = <Subscriptions app={app} />;
      break;
    case "transactions":
      page = <Transactions app={app} />;
      break;
    case "orders":
      page = <Orders app={app} />;
      break;
    case "inbox":
      page = <Inbox app={app} />;
      break;
    case "networth":
      page = <NetWorth />;
      break;
    default:
      page = section ? (
        <>
          <div className="ph">
            <h1>Settings</h1>
          </div>
          <Settings section={section} />
        </>
      ) : (
        <Empty title="Page not found">
          <Link href="/" className="acc">
            Go to Overview
          </Link>
        </Empty>
      );
  }

  return (
    <AppProvider value={app}>
      <div className="app">
        <aside className="side">
          <div className="brand">
            <Keyhole />
            <b>Tijori</b>
          </div>
          <Nav current={route?.key} />
          <div className="grow" />
          <Sync />
          <Who settings={!!section} />
        </aside>
        <main>
          {setupPending && !section && (
            <div className="notice" role="status">
              <span>Finish setting up Tijori: connect your mail and add a first statement so everything here stays current.</span>
              <Link href="/onboarding" className="btn2 sm">
                Resume setup
              </Link>
            </div>
          )}
          <section className="view">
            <PageBoundary key={path}>{page}</PageBoundary>
          </section>
        </main>
      </div>
      <nav className="tabbar" aria-label="Main">
        {ROUTES.filter((r) => r.tab).map((r) => (
          <Link key={r.key} href={r.path} className={route?.key === r.key ? "on" : ""} aria-current={route?.key === r.key ? "page" : undefined}>
            {G[r.key]}
            {r.tab}
          </Link>
        ))}
      </nav>
    </AppProvider>
  );
}

function Nav({ current }: { current: RouteKey | undefined }) {
  const inboxN = dataOf(read(api.inbox()))?.total ?? 0;
  return (
    <nav className="nav" aria-label="Main">
      {ROUTES.map((r) => (
        <Link key={r.key} href={r.path} className={current === r.key ? "on" : ""} aria-current={current === r.key ? "page" : undefined}>
          {G[r.key]}
          {r.title}
          {r.key === "inbox" && inboxN > 0 && (
            <span className="badge" aria-label={`${inboxN} to file`}>
              {inboxN}
            </span>
          )}
        </Link>
      ))}
    </nav>
  );
}

/** Mail source health: the collector lands in M1, so this reports the last connection test. */
function Sync() {
  const sources = dataOf(read(setup.mailSources()));
  const accounts = dataOf(read(api.accounts())) ?? [];
  if (!sources) return null;
  const ok = sources.find((m) => m.status === "ok");
  const bad = sources.find((m) => m.status === "error");
  const src = ok ?? bad ?? sources[0];
  const when = src?.last_tested_at ? relative(src.last_tested_at) : null;
  return (
    <Link href="/settings/sources" className="sync">
      <span className="l">
        <i className={ok ? "ok" : bad ? "err" : ""} />
        {!src ? "No mail connected" : ok ? `Mail connected${when ? ` · tested ${when}` : ""}` : bad ? "Mail login refused" : "Mail not tested"}
      </span>
      <small>
        {plural(accounts.length, "account")}
        {accounts.some((a) => a.last_statement) ? ` · last statement ${accounts.map((a) => a.last_statement?.period_end ?? "").sort().at(-1)!.slice(5).split("-").reverse().join("/")}` : ""}
      </small>
    </Link>
  );
}
function relative(ts: string): string {
  const m = Math.round((Date.now() - Date.parse(ts)) / 60000);
  if (m < 60) return `${Math.max(1, m)} min ago`;
  if (m < 1440) return `${Math.round(m / 60)} h ago`;
  return `${Math.round(m / 1440)} d ago`;
}

function Who({ settings }: { settings: boolean }) {
  const st = read(api.me());
  const me = dataOf(st);
  return (
    <div className="who">
      <div className="avatar">{me ? initials(me.name) : "·"}</div>
      <div className="grow">
        <b>{me?.name ?? (st.status === "loading" ? "…" : "You")}</b>
        <small>{me?.household?.name || "Personal"}</small>
      </div>
      <Link href="/settings/general" className={`gear${settings ? " on" : ""}`} aria-label="Settings" title="Settings">
        {G.gear}
      </Link>
    </div>
  );
}

function MonthSwitcher({ app }: { app: AppCtx }) {
  const i = app.month ? app.months.indexOf(app.month.key) : -1;
  if (!app.month) return null;
  return (
    <div className="monthpick">
      <button type="button" aria-label="Previous month" disabled={i <= 0} onClick={() => i > 0 && app.pick(app.months[i - 1]!)}>
        {G.left}
      </button>
      <span className="num" aria-live="polite">
        {app.settings.monthStartDay === 1 ? monthYear(app.month.key) : app.month.period.label}
      </span>
      <button type="button" aria-label="Next month" disabled={i < 0 || i >= app.months.length - 1} onClick={() => i >= 0 && i < app.months.length - 1 && app.pick(app.months[i + 1]!)}>
        {G.right}
      </button>
    </div>
  );
}

function openSearch() {
  if (location.pathname !== "/transactions") navigate("/transactions");
  setTimeout(() => document.querySelector<HTMLInputElement>(".search2 input")?.focus(), 50);
}
