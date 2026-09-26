import { BrandMark } from "../components/Icons";
import { Loading } from "../components/ui";
import { read } from "../lib/api";
import { dayShort } from "../lib/format";
import { setup } from "../lib/setup";
import { useStore } from "../lib/useStore";

/** /invite/:token: public. Shows whose household it is, then sends sign-in through /auth/login with the token. */
export function Invite({ token }: { token: string }) {
  useStore();
  const st = read(setup.invite(token));
  const info = st.status === "ready" ? st.data : null;
  const q = new URLSearchParams({ invite: token, next: "/onboarding/profile" });
  return (
    <div className="solo">
      <div className="card solo-card">
        <div className="brand solo-brand">
          <BrandMark />
          <b>Tijori</b>
        </div>
        {st.status === "loading" ? (
          <Loading card={false} />
        ) : info?.valid ? (
          <>
            <h1 className="solo-h">Join {info.household_name ?? "a household"} on Tijori</h1>
            <p className="sub">
              {info.inviter_name ? `${info.inviter_name} invited ` : "You're invited "}
              {info.email ? `${info.email}.` : "to join."} Your money stays yours: other members only see what you choose to share.
              {info.expires_at ? ` This invite expires ${dayShort(info.expires_at.slice(0, 10))}.` : ""}
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
              {st.status === "error" ? st.error.message : "It may have expired or already been used. Ask the person who invited you for a new link."}
            </p>
          </>
        )}
      </div>
    </div>
  );
}
