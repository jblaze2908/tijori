import { useAction } from "../components/forms";
import { CardHead } from "../components/ui";
import { Link, navigate } from "../lib/router";
import { signOut } from "../lib/setup";
import { MailSources } from "./setup/mail";
import { AccountsForm, ProfileForm } from "./setup/profile";
import { HouseholdPanel, StatementPasswords } from "./setup/secrets";

export const SECTIONS = [
  ["general", "General"],
  ["mail", "Mail sources"],
  ["accounts", "Accounts"],
  ["passwords", "Statement passwords"],
  ["household", "Household"],
] as const;
export type Section = (typeof SECTIONS)[number][0];

export function Settings({ section }: { section: Section }) {
  const out = useAction();
  return (
    <>
      <nav className="subnav" aria-label="Settings">
        {SECTIONS.map(([k, l]) => (
          <Link key={k} href={`/settings/${k}`} className={k === section ? "on" : ""} aria-current={k === section ? "page" : undefined}>
            {l}
          </Link>
        ))}
      </nav>
      <div className="card settings-card">
        {section === "general" && (
          <>
            <CardHead title="Profile" />
            <ProfileForm submitLabel="Save" />
            <CardHead title="Setup" />
            <p className="sub">
              Walk through the guided setup again: mail, the label filter, statement passwords and the first import.{" "}
              <Link href="/onboarding" className="acc">
                Open setup
              </Link>
            </p>
            <CardHead title="Session" />
            <div className="row mt">
              <button type="button" className="btn ghost" disabled={out.busy} onClick={() => out.run(signOut).then((r) => r.ok && navigate("/welcome", { replace: true }))}>
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
        {section === "accounts" && (
          <>
            <CardHead title="Accounts" />
            <AccountsForm />
          </>
        )}
        {section === "passwords" && (
          <>
            <CardHead title="Statement passwords" x="write-only" />
            <StatementPasswords />
          </>
        )}
        {section === "household" && (
          <>
            <CardHead title="Household" />
            <HouseholdPanel />
          </>
        )}
      </div>
    </>
  );
}
