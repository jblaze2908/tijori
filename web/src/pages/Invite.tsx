import { BrandMark } from "../components/Icons";
import { Loading } from "../components/ui";
import { read } from "../lib/api";
import { dayShort } from "../lib/format";
import { setup } from "../lib/setup";
import { useStore } from "../lib/useStore";

const STATUS_TEXT = {
  used: "This invite has already been used. If that wasn't you, ask for a new link.",
  expired: "This invite has expired. Ask the person who invited you for a new link.",
} as const;

/** /invite/:token, public: GET /api/invites/{token} needs no sign-in. Sign-in must use exactly the invited email. */
export function Invite({ token }: { token: string }) {
  useStore();
  const st = read(setup.invite(token));
  const info = st.status === "ready" ? st.data : null;
  const household = info ? (typeof info.household === "string" ? info.household : info.household?.name) : null;
  const q = new URLSearchParams({ invite: token, return_to: "/onboarding" });
  return (
    <div className="solo">
      <div className="card solo-card">
        <div className="brand solo-brand">
          <BrandMark />
          <b>Tijori</b>
        </div>
        {st.status === "loading" ? (
          <Loading card={false} />
        ) : info?.status === "valid" ? (
          <>
            <h1 className="solo-h">Join {household ?? "a household"} on Tijori</h1>
            <p className="sub">
              This invite is for <b className="t1">{info.email}</b>; sign in with that Google account. Your money stays yours: other members only see
              what you choose to share. It expires {dayShort(info.expires_at.slice(0, 10))}.
            </p>
            {/* A full page load on purpose: /auth/login is a server redirect to Google. */}
            <a className="btn btn-lg" href={`/auth/login?${q}`}>
              Sign in with Google to join
            </a>
          </>
        ) : (
          <>
            <h1 className="solo-h">This invite can't be used</h1>
            <p className="sub">
              {info ? STATUS_TEXT[info.status] : st.status === "error" ? st.error.message : "The link isn't valid. Ask the person who invited you for a new one."}
            </p>
          </>
        )}
      </div>
    </div>
  );
}
