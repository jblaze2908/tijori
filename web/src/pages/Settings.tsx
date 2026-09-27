import { useAction } from "../components/forms";
import { CardHead } from "../components/ui";
import { Link, navigate } from "../lib/router";
import { signOut } from "../lib/setup";
import { MailSources } from "./setup/mail";
import { ProfileForm } from "./setup/profile";
import { HouseholdPanel, StatementPasswords } from "./setup/secrets";
import { McpTokens, NotifyRetention } from "./Prefs";
import { PayeeNames, Rules, Sources } from "./Sources";

export const SECTIONS = [
  ["general", "General"],
  ["mail", "Mail"],
  ["sources", "Accounts & sources"],
  ["passwords", "Statement passwords"],
  ["household", "Household"],
  ["rules", "Categories & rules"],
  ["notify", "Notifications & retention"],
  ["claude", "Claude (MCP)"],
] as const;
export type Section = (typeof SECTIONS)[number][0] | "accounts" | "upload";

export function Settings({ section }: { section: Section }) {
  const out = useAction();
  const current = section === "accounts" || section === "upload" ? "sources" : section;
  return (
    <div className="settings-wrap">
      <nav className="subnav2" aria-label="Settings">
        {SECTIONS.map(([k, l]) => (
          <Link key={k} href={`/settings/${k}`} className={k === current ? "on" : ""} aria-current={k === current ? "page" : undefined}>
            {l}
          </Link>
        ))}
      </nav>
      <div className="content">
      {current === "sources" && <Sources />}
      {current === "rules" && (
        <>
          <Rules />
          <PayeeNames />
        </>
      )}
      {!["sources", "rules"].includes(current) && <div className="card settings-card">
        {section === "general" && (
          <>
            <CardHead title="Profile" />
            <ProfileForm submitLabel="Save" />
            <CardHead title="Setup" />
            <p className="sub">
              Walk through the guided setup again: profile, accounts, mail, the label, statement passwords and a first statement.{" "}
              <Link href="/onboarding/profile" className="acc">
                Open setup
              </Link>
            </p>
            <CardHead title="Session" />
            <div className="row mt">
              <button
                type="button"
                className="btn ghost"
                disabled={out.busy}
                onClick={async () => {
                  if ((await out.run(signOut)).ok) navigate("/welcome", { replace: true });
                }}
              >
                Sign out
              </button>
              {out.error && <span className="bad">{out.error}</span>}
            </div>
          </>
        )}
        {section === "mail" && (
          <>
            <CardHead title="Mail sources" x="IMAP, read-only, one label" />
            <MailSources />
          </>
        )}

        {section === "passwords" && (
          <>
            <CardHead title="Statement passwords" x="write-only" />
            <StatementPasswords />
          </>
        )}

        {section === "notify" && (
          <>
            <CardHead title="Notifications & retention" />
            <NotifyRetention />
          </>
        )}
        {section === "claude" && (
          <>
            <CardHead title="Claude (MCP)" x="read + categorize" />
            <McpTokens />
          </>
        )}
        {section === "household" && (
          <>
            <CardHead title="Household" />
            <HouseholdPanel />
          </>
        )}
      </div>}
      </div>
    </div>
  );
}
