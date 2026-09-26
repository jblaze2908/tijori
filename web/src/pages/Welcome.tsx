import { BrandMark } from "../components/Icons";
import { AUTH_ERROR } from "../lib/setup";

/** Only same-origin app paths survive as return_to (the server re-checks), so sign-in can't become an open redirect. */
const safeReturn = (p: string | null) => (p && /^\/(?!\/)[\w\-/?=&%.]*$/.test(p) && !p.startsWith("/auth") && !p.startsWith("/welcome") ? p : "/");

export function Welcome({ next, error }: { next: string | null; error: string | null }) {
  const q = new URLSearchParams({ return_to: safeReturn(next) });
  const message = error ? (AUTH_ERROR[error] ?? "Sign-in didn't complete. Try again.") : null;
  return (
    <div className="solo">
      <div className="card solo-card">
        <div className="brand solo-brand">
          <BrandMark />
          <b>Tijori</b>
        </div>
        <h1 className="solo-h">Your household's money, in one private place.</h1>
        <p className="sub">
          Tijori reads bank alerts and statements you choose to share, files them for you, and keeps your net worth current. It runs on your
          family's own server.
        </p>
        {message && (
          <div className="result bad mt" role="alert">
            {message}
          </div>
        )}
        {/* A full page load on purpose: /auth/login is a server redirect to Google, not an app route. */}
        <a className="btn btn-lg" href={`/auth/login?${q}`}>
          <GoogleG /> Sign in with Google
        </a>
        <p className="sub fine">Only household members and invited people can sign in.</p>
      </div>
    </div>
  );
}

const GoogleG = () => (
  <svg width="16" height="16" viewBox="0 0 48 48" aria-hidden>
    <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
    <path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
    <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z" />
    <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" />
  </svg>
);
