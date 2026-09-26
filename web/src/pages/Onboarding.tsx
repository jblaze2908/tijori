import { useEffect } from "react";
import { BrandMark } from "../components/Icons";
import { Loading } from "../components/ui";
import { dataOf, read } from "../lib/api";
import { navigate } from "../lib/router";
import { finishOnboarding, mailErrorText, saveStep, setup, STEP_LABEL, STEPS, type Onboarding as OnboardingState, type Step } from "../lib/setup";
import { useStore } from "../lib/useStore";
import { LabelSetup, MailConnect } from "./setup/mail";
import { AccountsSettings, ProfileForm } from "./setup/profile";
import { FirstUpload, StatementPasswords } from "./setup/secrets";

const INTRO: Record<Step, string> = {
  profile: "Your name, and the day your month starts.",
  accounts: "The banks and cards whose mail Tijori reads, and how banks print your name, so money moving between your own accounts isn't counted as spending.",
  mail: "Tijori reads bank emails over IMAP with an app password: read-only, from one label.",
  label: "A filter puts bank emails in one label, and IMAP is limited to that label.",
  statement_passwords: "Statements arrive as locked PDFs. Save each bank's statement password so they can be read.",
  first_upload: "Upload one statement to see Tijori read, reconcile and file it.",
};

/** Where the server's step (docs/api.md) resumes in the UI: "mail" lands on the label step once a source exists. */
export function resumeStep(o: OnboardingState, hasMailSource: boolean): Step {
  if (o.step === "mail") return hasMailSource ? "label" : "mail";
  return o.step === "done" ? "first_upload" : o.step;
}

/** A step is ticked from the server's checklist (derived from real data), or by the member having moved past it. */
function done(o: OnboardingState | null, s: Step, mailOk: boolean): boolean {
  if (!o) return false;
  if (s === "profile") return o.step !== "profile" || o.checklist.profile;
  if (s === "accounts") return o.checklist.profile;
  if (s === "mail") return o.checklist.mail_source;
  if (s === "label") return mailOk;
  if (s === "statement_passwords") return o.checklist.statement_passwords;
  return o.checklist.first_upload;
}

export function Onboarding({ step }: { step: string }) {
  useStore();
  const st = read(setup.onboarding());
  const sources = read(setup.mailSources());
  const o = dataOf(st);
  const srcList = dataOf(sources) ?? [];
  const current = (STEPS as readonly string[]).includes(step) ? (step as Step) : null;

  useEffect(() => {
    document.title = current ? `${STEP_LABEL[current]} · Set up Tijori` : "Set up Tijori";
  }, [current]);

  // /onboarding (or an unknown step) resumes where the server says, or starts at the beginning.
  useEffect(() => {
    if (current || st.status === "loading" || sources.status === "loading") return;
    navigate(o ? (o.completed_at ? "/" : `/onboarding/${resumeStep(o, srcList.length > 0)}`) : `/onboarding/${STEPS[0]}`, { replace: true });
  }, [current, st.status, sources.status, o, srcList.length]);

  if (!current) return <Loading />;
  const i = STEPS.indexOf(current);
  const mailOk = srcList.some((s) => s.status === "ok");
  // Resume state is best-effort: a server without /api/onboarding still lets the member move through.
  const go = async (next: Step) => {
    await saveStep(next).catch(() => undefined);
    navigate(`/onboarding/${next}`);
  };
  const next = () => {
    const n = STEPS[i + 1];
    if (n) void go(n);
    else void finish();
  };
  const finish = async () => {
    await finishOnboarding().catch(() => undefined);
    navigate("/");
  };

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
            const ticked = done(o, s, mailOk);
            const state = s === current ? "on" : ticked ? "done" : "";
            return (
              <li key={s} className={state} aria-current={s === current ? "step" : undefined}>
                <button type="button" onClick={() => navigate(`/onboarding/${s}`)}>
                  <span className="n" aria-hidden>
                    {ticked ? "✓" : j + 1}
                  </span>
                  <span className="t">{STEP_LABEL[s]}</span>
                  {ticked && <span className="sr-only"> (done)</span>}
                </button>
              </li>
            );
          })}
        </ol>
        <h1 className="solo-h">{STEP_LABEL[current]}</h1>
        <p className="sub">{INTRO[current]}</p>
        <div className="step-body">
          {current === "profile" && <ProfileForm submitLabel="Save and continue" onSaved={next} />}
          {current === "accounts" && <AccountsSettings onContinue={next} />}
          {current === "mail" && <MailStep onContinue={next} />}
          {current === "label" && <LabelSetup onContinue={next} />}
          {current === "statement_passwords" && <StatementPasswords onContinue={next} />}
          {current === "first_upload" && <FirstUpload onDone={finish} />}
        </div>
        <div className="wizard-foot">
          {i > 0 ? (
            <button type="button" className="btn ghost" onClick={() => navigate(`/onboarding/${STEPS[i - 1]}`)}>
              ← Back
            </button>
          ) : (
            <span />
          )}
          {current !== "first_upload" && (
            <button type="button" className="linkish" onClick={next}>
              Skip for now
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/** A member who already connected mail can move on; the form stays available to add another. */
function MailStep({ onContinue }: { onContinue: () => void }) {
  const sources = dataOf(read(setup.mailSources())) ?? [];
  return (
    <>
      {sources.map((s) =>
        s.status === "ok" ? (
          <div className="result ok" role="status" key={s.id}>
            Connected: {s.email}.{" "}
            <button type="button" className="linkish nm" onClick={onContinue}>
              Continue
            </button>
          </div>
        ) : (
          <div className="result bad" role="status" key={s.id}>
            {s.email} is added but {s.status === "untested" ? "not tested yet" : `failing: ${mailErrorText(s.last_error_code)}`} Test or replace its password in Settings → Mail sources.
          </div>
        ),
      )}
      <MailConnect onConnected={onContinue} />
    </>
  );
}
