import { useEffect, useState, type ReactNode } from "react";
import { BrandMark, ICONS, SearchIcon } from "./components/Icons";
import { Empty } from "./components/ui";
import { AppProvider, buildCtx, type AppCtx } from "./ctx";
import { api, dataOf, read, retryFailed } from "./lib/api";
import { initials, monthYear, todayIST } from "./lib/format";
import { Link, match, navigate, useLocation } from "./lib/router";
import { setup } from "./lib/setup";
import { useStore } from "./lib/useStore";
import type { MonthKey } from "./lib/types";
import { Activity, focusActivitySearch } from "./pages/Activity";
import { Inbox } from "./pages/Inbox";
import { NetWorth } from "./pages/NetWorth";
import { Invite } from "./pages/Invite";
import { Onboarding } from "./pages/Onboarding";
import { Overview } from "./pages/Overview";
import { SECTIONS, Settings, type Section } from "./pages/Settings";
import { Trends } from "./pages/Trends";
import { Welcome } from "./pages/Welcome";

type RouteKey = "overview" | "activity" | "trends" | "inbox" | "networth";
const ROUTES: { path: string; key: RouteKey; title: string }[] = [
  { path: "/", key: "overview", title: "Overview" },
  { path: "/activity", key: "activity", title: "Activity" },
  { path: "/trends", key: "trends", title: "Trends" },
  { path: "/inbox", key: "inbox", title: "Inbox" },
  { path: "/networth", key: "networth", title: "Net worth" },
];
const LATER = [
  ["budgets", "Budgets"],
  ["subs", "Subscriptions"],
] as const;
const MONTHLESS: ReadonlySet<RouteKey> = new Set(["inbox", "networth"]);
/** Pages reachable while signed out; everything else sends a 401 on /api/me to /welcome. */
const PUBLIC = /^\/(welcome|invite\/[^/]+)$/;

const MONTH_KEY = "tj.month";
const IS_MAC = /Mac|iPhone|iPad/.test(navigator.userAgent);

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
  const route = ROUTES.find((r) => r.path === path);
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
      page = <Overview app={app} />;
      break;
    case "activity":
      page = <Activity app={app} />;
      break;
    case "trends":
      page = <Trends app={app} />;
      break;
    case "inbox":
      page = <Inbox />;
      break;
    case "networth":
      page = <NetWorth />;
      break;
    default:
      page = section ? (
        <Settings section={section} />
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
            <BrandMark />
            <b>Tijori</b>
          </div>
          <Nav current={route?.key} settings={!!section} />
          <div className="grow" />
          <Who />
        </aside>
        <main>
          <div className="top">
            <h1>{route?.title ?? (section ? "Settings" : "Tijori")}</h1>
            <div className="sp" />
            <button type="button" className="search" onClick={openSearch}>
              <SearchIcon />
              Search<kbd>{IS_MAC ? "⌘K" : "Ctrl K"}</kbd>
            </button>
            <MonthSwitcher app={app} hidden={!route || MONTHLESS.has(route.key) || (route.key === "activity" && params.has("from"))} />
            <Link href="/settings/general" className={`gear${section ? " on" : ""}`} aria-label="Settings" title="Settings">
              {ICONS.settings}
            </Link>
          </div>
          <section className="view">
            {setupPending && !section && (
              <div className="notice" role="status">
                <span>Finish setting up Tijori: connect your mail and add a first statement so everything here stays current.</span>
                <Link href="/onboarding" className="btn ghost">
                  Resume setup
                </Link>
              </div>
            )}
            {page}
          </section>
        </main>
      </div>
      <nav className="tabbar" aria-label="Main">
        {ROUTES.map((r) => (
          <Link key={r.key} href={r.path} className={route?.key === r.key ? "on" : ""} aria-current={route?.key === r.key ? "page" : undefined}>
            {ICONS[r.key]}
            {r.title}
          </Link>
        ))}
      </nav>
    </AppProvider>
  );
}

function Nav({ current, settings }: { current: RouteKey | undefined; settings: boolean }) {
  const inboxN = dataOf(read(api.inbox()))?.items.length ?? 0;
  return (
    <nav className="nav" aria-label="Main">
      {ROUTES.map((r) => (
        <Link key={r.key} href={r.path} className={current === r.key ? "on" : ""} aria-current={current === r.key ? "page" : undefined}>
          {ICONS[r.key]}
          {r.title}
          {r.key === "inbox" && inboxN > 0 && (
            <span className="badge num" aria-label={`${inboxN} to file`}>
              {inboxN}
            </span>
          )}
        </Link>
      ))}
      <div className="gap" />
      {LATER.map(([k, label]) => (
        <a key={k} className="later" aria-disabled="true">
          {ICONS[k]}
          {label}
          <span className="soon">soon</span>
        </a>
      ))}
      <div className="gap" />
      <Link href="/settings/general" className={settings ? "on" : ""} aria-current={settings ? "page" : undefined}>
        {ICONS.settings}
        Settings
      </Link>
    </nav>
  );
}

function Who() {
  const st = read(api.me());
  const me = dataOf(st);
  return (
    <div className="who">
      <div className="avatar">{me ? initials(me.name) : "·"}</div>
      <div>
        <div className="b">{me?.name ?? (st.status === "loading" ? "…" : "You")}</div>
        <small>{me?.household?.name || "Personal"}</small>
      </div>
    </div>
  );
}

function MonthSwitcher({ app, hidden }: { app: AppCtx; hidden: boolean }) {
  const i = app.month ? app.months.indexOf(app.month.key) : -1;
  return (
    <div className={`month${hidden || !app.month ? " hidden" : ""}`}>
      <button type="button" aria-label="Previous month" disabled={i <= 0} onClick={() => i > 0 && app.pick(app.months[i - 1]!)}>
        ‹
      </button>
      <span className="num" aria-live="polite">
        {app.month ? (app.settings.monthStartDay === 1 ? monthYear(app.month.key) : app.month.period.label) : ""}
      </span>
      <button type="button" aria-label="Next month" disabled={i < 0 || i >= app.months.length - 1} onClick={() => i >= 0 && i < app.months.length - 1 && app.pick(app.months[i + 1]!)}>
        ›
      </button>
    </div>
  );
}

function openSearch() {
  focusActivitySearch();
  if (location.pathname !== "/activity") navigate("/activity");
}
