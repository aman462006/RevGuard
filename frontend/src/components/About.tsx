// About RevGuard — explains the product itself (not a company page). Every feature listed here
// is actually implemented in the codebase (detection, Gemini diagnosis, PolicyEngine, executor,
// verifier, audit, evaluation, Razorpay Test Mode, human override).

const STAGES = [
  {
    n: "01",
    name: "Detect",
    body: "RevGuard monitors revenue-risk signals and identifies cases where money is at risk.",
  },
  {
    n: "02",
    name: "Diagnose",
    body: "The configured AI model analyzes the case and recommends the appropriate intervention.",
  },
  {
    n: "03",
    name: "Policy",
    body: "A deterministic PolicyEngine decides whether the recommended action is actually allowed. The AI does not have unrestricted authority.",
  },
  {
    n: "04",
    name: "Execute",
    body: "Only authorized actions are executed through the configured provider / test environment.",
  },
  {
    n: "05",
    name: "Verify",
    body: "RevGuard checks whether the intended outcome actually occurred. Only verified recovery contributes to recovered revenue.",
  },
];

const FEATURES = [
  "Revenue-risk detection across four workflows",
  "AI diagnosis and recommendations",
  "Deterministic policy authorization",
  "Bounded recovery execution",
  "Recovery verification (verified-only accounting)",
  "Human-in-the-loop override for escalated cases",
  "Case management with full audit trail",
  "Synthetic demo scenarios",
  "Baseline vs. agent evaluation on a seeded dataset",
  "Razorpay Test Mode integration + webhook reconciliation",
];

const ARCH = [
  "Revenue Signals",
  "Risk Detection",
  "AI Diagnosis",
  "PolicyEngine",
  "Authorized Executor",
  "Verification",
  "Recovered Revenue / Escalation / Stop",
];

interface Props {
  onEnter?: () => void;
  onDemo?: () => void;
}

export function About({ onEnter, onDemo }: Props) {
  return (
    <div>
      <section className="about-hero">
        <div className="eyebrow">About RevGuard</div>
        <h1>
          An AI revenue recovery agent that detects risk, decides what to do, acts within policy,
          and verifies the outcome.
        </h1>
        {(onEnter || onDemo) && (
          <div className="btn-row" style={{ marginTop: 26 }}>
            {onEnter && (
              <button className="btn primary" onClick={onEnter}>
                Open the console
              </button>
            )}
            {onDemo && (
              <button className="btn" onClick={onDemo}>
                Run a demo scenario
              </button>
            )}
          </div>
        )}
      </section>

      {/* A */}
      <section className="about-section">
        <div className="eyebrow muted">What is RevGuard?</div>
        <h2>Recovering revenue that would otherwise be lost</h2>
        <p>
          RevGuard identifies revenue that is likely to be lost and determines an appropriate
          recovery action. Rather than simply flagging problems, it drives a bounded workflow from
          detection through verified recovery.
        </p>
        <p className="muted small" style={{ marginTop: 8 }}>
          It covers four revenue-risk workflows:
        </p>
        <ul className="about-list">
          <li>Failed subscriptions — a recurring charge was declined</li>
          <li>Checkout abandonment — a customer left with items in the cart</li>
          <li>Overdue receivables — an invoice is past due</li>
          <li>Payment degradation — a spike in failures for a payment method</li>
        </ul>
      </section>

      {/* B */}
      <section className="about-section">
        <div className="eyebrow muted">How it works</div>
        <h2>A five-stage architecture</h2>
        <div className="about-stages">
          {STAGES.map((s) => (
            <div className="about-stage" key={s.n}>
              <div className="about-stage-num">{s.n}</div>
              <div>
                <h3>{s.name}</h3>
                <p>{s.body}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* C */}
      <section className="about-section">
        <div className="eyebrow muted">Why the policy layer exists</div>
        <h2>The AI recommends; it does not have unrestricted authority</h2>
        <p>
          The AI recommends actions, but a deterministic PolicyEngine decides what can actually
          happen. This separation is what makes the system safe and auditable.
        </p>
        <p>The PolicyEngine provides deterministic controls around:</p>
        <ul className="about-list">
          <li>Escalation to human review</li>
          <li>Stopping rules and bounded retries</li>
          <li>Bounded recovery actions within an amount-at-risk cap</li>
          <li>Human override and authorization</li>
        </ul>
      </section>

      {/* D */}
      <section className="about-section">
        <div className="eyebrow muted">Features</div>
        <h2>What is implemented today</h2>
        <ul className="about-list">
          {FEATURES.map((f) => (
            <li key={f}>{f}</li>
          ))}
        </ul>
      </section>

      {/* E */}
      <section className="about-section">
        <div className="eyebrow muted">How to test it</div>
        <h2>A five-minute walkthrough</h2>
        <ol className="steps">
          <li>Open the Demo tab and pick a scenario.</li>
          <li>Click Create &amp; Run — the app switches to Overview and shows the agent journey.</li>
          <li>Open the case to inspect the full lifecycle (Detect → Diagnose → Policy → Execute → Verify).</li>
          <li>
            Complete recovery: in demo, use <strong>Simulate payment received</strong>; in
            production, pay the Razorpay Test Mode link and click <strong>Check payment status</strong>.
          </li>
          <li>Use <strong>Reset all cases</strong> on the Cases tab to clear everything and run again from scratch.</li>
          <li>Open Evaluation to compare the RevGuard agent against the baseline.</li>
        </ol>
        <p className="muted small" style={{ marginTop: 14 }}>
          What to look for: the detected risk, the AI recommendation, the policy decision, the
          execution result, the verification result, and the recovered amount.
        </p>
      </section>

      {/* E2 — Demo vs Production, and what is actually real */}
      <section className="about-section">
        <div className="eyebrow muted">Demo vs Production</div>
        <h2>What is synthetic, and what is really connected</h2>
        <p>
          The current mode is shown honestly at the top of every screen (via <code>GET /status</code>
          ), and it is never inferred or silently changed.
        </p>
        <ul className="about-list">
          <li>
            <strong>Demo mode</strong> — a credential-free rehearsal. Detection and the PolicyEngine
            are the real ones, but diagnosis, execution, and verification use deterministic offline
            test doubles. Nothing contacts a live provider.
          </li>
          <li>
            <strong>Production mode</strong> — the live path. The configured <strong>AI model</strong> diagnoses,
            approved actions create real <strong>Razorpay Test Mode</strong> orders / payment links,
            and recovery is confirmed from the verified Razorpay payment status. If it is not fully
            configured, running a case returns <code>503</code> — it never silently falls back to a
            mock.
          </li>
          <li>
            <strong>Synthetic data</strong> — every demo scenario is generated test data (never real
            customer money) and is labelled <span className="prov-tag synthetic">Synthetic</span>{" "}
            everywhere it appears, including in the metrics.
          </li>
          <li>
            <strong>Verified recovery only</strong> — money counts as recovered only when the
            provider confirms payment (a Razorpay <em>paid</em> status or a signature-verified
            webhook). A successful API call alone is never counted, and the recovered amount can
            never exceed the amount at risk.
          </li>
        </ul>
        <p className="muted small" style={{ marginTop: 8 }}>
          To create a real Razorpay Test Mode intervention: set <code>REVGUARD_MODE=production</code>
          {" "}with the AI provider API key and Razorpay Test Mode keys, run a case, then complete (or
          simulate) the test payment to see verified recovery reflected in the metrics.
        </p>
      </section>

      {/* F */}
      <section className="about-section">
        <div className="eyebrow muted">How to use RevGuard</div>
        <h2>The operator workflow</h2>
        <ol className="steps">
          <li>Review detected revenue-risk cases.</li>
          <li>Open a case.</li>
          <li>Understand why it was flagged.</li>
          <li>Review the AI recommendation.</li>
          <li>Review the PolicyEngine decision.</li>
          <li>Inspect execution.</li>
          <li>Confirm verification.</li>
          <li>Use the audit trail to understand the complete decision path.</li>
        </ol>
      </section>

      {/* G */}
      <section className="about-section">
        <div className="eyebrow muted">Architecture overview</div>
        <h2>From signal to recovered revenue</h2>
        <div className="about-arch">
          {ARCH.map((node, i) => (
            <div key={node}>
              <div className={`arch-node ${i === ARCH.length - 1 ? "accent" : ""}`}>{node}</div>
              {i < ARCH.length - 1 && <div className="arch-sep">↓</div>}
            </div>
          ))}
        </div>
        <p className="note">
          AI is advisory. The PolicyEngine is authoritative. Recovery is verification-based.
        </p>
      </section>
    </div>
  );
}
