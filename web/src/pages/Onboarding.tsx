import { useEffect } from "react";
import { BrandMark } from "../components/Icons";
import { Loading } from "../components/ui";
import { read } from "../lib/api";
import { navigate } from "../lib/router";
import { saveStep, setup, STEP_LABEL, STEPS, type Step } from "../lib/setup";
import { useStore } from "../lib/useStore";
import { GmailSetup, MailConnect } from "./setup/mail";
import { AccountsForm, ProfileForm } from "./setup/profile";
import { Backfill, StatementPasswords } from "./setup/secrets";

const INTRO: Record<Step, string> = {
  profile: "Tell Tijori who you are and when your month starts.",
  accounts: "List the accounts whose alerts and statements reach your mail, so Tijori can file them and spot transfers between them.",
  mail: "Tijori reads bank emails over IMAP with an app password, read-only, from one label.",
  gmail: "A filter puts bank emails in one label, and the app password is limited to that label.",
  passwords: "Statements arrive as locked PDFs. Add each bank's statement password so they can be read.",
  backfill: "Tijori imports what's already in the label. This can take a few minutes.",
};

/** Six steps with a progress rail; the server remembers the step (PATCH /api/onboarding) so setup resumes where it left off. */
export function Onboarding({ step }: { step: string }) {
  useStore();
  const st = read(setup.onboarding());
  const current = (STEPS as readonly string[]).includes(step) ? (step as Step) : null;

  useEffect(() => {
    document.title = current ? `${STEP_LABEL[current]} · Set up Tijori` : "Set up Tijori";
  }, [current]);

  // /onboarding (or an unknown step) resumes at the server's step, or starts at the beginning.
  useEffect(() => {
    if (current || st.status === "loading") return;
    const saved = st.status === "ready" ? st.data?.step : null;
    navigate(saved === "done" ? "/" : `/onboarding/${saved ?? STEPS[0]}`, { replace: true });
  }, [current, st]);

  if (!current) return <Loading />;
  const i = STEPS.indexOf(current);
  const completed = new Set(st.status === "ready" ? (st.data?.completed ?? []) : []);
  const go = async (next: Step | "done") => {
    // Resume state is best-effort: a server without /api/onboarding still lets the member move through.
    await saveStep(next).catch(() => undefined);
    navigate(next === "done" ? "/" : `/onboarding/${next}`);
  };
  const next = () => go(STEPS[i + 1] ?? "done");

  return (
    <div className="solo">
      <div className="card solo-card solo-wide">
        <div className="brand solo-brand">
          <BrandMark />
          <b>Tijori</b>
          <span className="sub ml-a">
            Step {i + 1} of {STEPS.length}
          </span>
        </div>
        <ol className="steps" aria-label="Setup progress">
          {STEPS.map((s, j) => {
            const state = s === current ? "on" : completed.has(s) || j < i ? "done" : "";
            return (
              <li key={s} className={state} aria-current={s === current ? "step" : undefined}>
                {j <= i || completed.has(s) ? (
                  <button type="button" onClick={() => navigate(`/onboarding/${s}`)}>
                    <span className="n" aria-hidden>
                      {state === "done" ? "✓" : j + 1}
                    </span>
                    <span className="t">{STEP_LABEL[s]}</span>
                  </button>
                ) : (
                  <span className="disabled">
                    <span className="n" aria-hidden>
                      {j + 1}
                    </span>
                    <span className="t">{STEP_LABEL[s]}</span>
                  </span>
                )}
              </li>
            );
          })}
        </ol>
        <h1 className="solo-h">{STEP_LABEL[current]}</h1>
        <p className="sub">{INTRO[current]}</p>
        <div className="step-body">
          {current === "profile" && <ProfileForm submitLabel="Save and continue" onSaved={next} />}
          {current === "accounts" && <AccountsForm onContinue={next} />}
          {current === "mail" && <MailStep onContinue={next} />}
          {current === "gmail" && <GmailSetup onContinue={next} />}
          {current === "passwords" && <StatementPasswords onContinue={next} />}
          {current === "backfill" && <Backfill onDone={() => go("done")} />}
        </div>
        <div className="wizard-foot">
          {i > 0 ? (
            <button type="button" className="btn ghost" onClick={() => navigate(`/onboarding/${STEPS[i - 1]}`)}>
              ← Back
            </button>
          ) : (
            <span />
          )}
          {current !== "backfill" && (
            <button type="button" className="linkish" onClick={next}>
              Skip for now
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/** A member who already connected mail can move on; otherwise the connect form is the step. */
function MailStep({ onContinue }: { onContinue: () => void }) {
  const sources = read(setup.mailSources());
  const have = sources.status === "ready" ? (sources.data ?? []) : [];
  return (
    <>
      {have.length > 0 && (
        <div className="result ok" role="status">
          Connected: {have.map((s) => s.email).join(", ")}.{" "}
          <button type="button" className="linkish nm" onClick={onContinue}>
            Continue
          </button>
        </div>
      )}
      <MailConnect onSaved={onContinue} submitLabel={have.length ? "Add and continue" : "Save and continue"} />
    </>
  );
}
