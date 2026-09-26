import { createElement, useSyncExternalStore, type AnchorHTMLAttributes, type MouseEvent } from "react";

const NAV_EVENT = "tj:navigate";

function subscribe(f: () => void) {
  addEventListener("popstate", f);
  addEventListener(NAV_EVENT, f);
  return () => {
    removeEventListener("popstate", f);
    removeEventListener(NAV_EVENT, f);
  };
}
const snapshot = () => location.pathname + location.search;

export function navigate(to: string, opts: { replace?: boolean } = {}) {
  if (to === snapshot()) return;
  if (opts.replace) history.replaceState(null, "", to);
  else history.pushState(null, "", to);
  dispatchEvent(new Event(NAV_EVENT));
}

export function useLocation(): { path: string; params: URLSearchParams } {
  const s = useSyncExternalStore(subscribe, snapshot);
  const i = s.indexOf("?");
  return { path: i < 0 ? s : s.slice(0, i), params: new URLSearchParams(i < 0 ? "" : s.slice(i)) };
}

/** Matches "/invite/:token"-style patterns; returns the params, or null. */
export function match(pattern: string, path: string): Record<string, string> | null {
  const a = pattern.split("/");
  const b = path.split("/");
  if (a.length !== b.length) return null;
  const out: Record<string, string> = {};
  for (let i = 0; i < a.length; i++) {
    const seg = a[i]!;
    if (seg.startsWith(":")) {
      if (!b[i]) return null;
      out[seg.slice(1)] = decodeURIComponent(b[i]!);
    } else if (seg !== b[i]) return null;
  }
  return out;
}

/** An <a> that routes in-app on a plain left click and leaves new-tab/modifier clicks to the browser. */
export function Link(props: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) {
  const onClick = (e: MouseEvent<HTMLAnchorElement>) => {
    props.onClick?.(e);
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    navigate(props.href);
  };
  return createElement("a", { ...props, onClick });
}
