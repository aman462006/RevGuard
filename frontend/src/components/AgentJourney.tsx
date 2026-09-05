import type { JourneyStep } from "../journey";

// Renders the six-stage agent journey as a vertical, connected stepper. Purely presentational:
// all content comes from buildJourney() which reads real case + audit data.
export function AgentJourney({
  steps,
  compact = false,
}: {
  steps: JourneyStep[];
  compact?: boolean;
}) {
  return (
    <ol className={`journey ${compact ? "journey-compact" : ""}`}>
      {steps.map((s) => (
        <li key={s.key} className={`jstep tone-${s.tone} state-${s.state}`}>
          <div className="jstep-rail" aria-hidden="true">
            <span className="jstep-dot" />
          </div>
          <div className="jstep-body">
            <div className="jstep-label">{s.label}</div>
            <div className="jstep-headline">{s.headline}</div>
            {!compact &&
              s.lines.map((line, i) => (
                <div className="jstep-line" key={i}>
                  {line}
                </div>
              ))}
            {compact && s.lines[0] && <div className="jstep-line">{s.lines[0]}</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}
