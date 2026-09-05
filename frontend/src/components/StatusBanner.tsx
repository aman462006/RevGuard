import type { StatusInfo } from "../types";
import { providerLabel } from "../journey";

// A restrained status strip showing runtime mode and provider readiness. Never shows a
// credential — only names and yes/no readiness flags returned by GET /status.
export function StatusStrip({ status }: { status: StatusInfo | null }) {
  if (!status) return null;

  const demo = status.demo;
  const notReady = !demo && !status.run_ready;
  const provider = providerLabel(status.ai_provider);
  // Explicit AI-provider readiness — only a name + yes/no state, never a credential.
  const aiLabel = demo
    ? "Mock (offline)"
    : `${provider} · ${status.ai_configured ? "Ready" : "Not configured"}`;

  return (
    <div className="statusstrip">
      <span className={`mode ${demo ? "demo" : "prod"}`}>{demo ? "Demo" : "Production"}</span>
      <span>
        {demo
          ? "Credential-free rehearsal using deterministic test doubles"
          : `Live path — ${provider} + Razorpay Test Mode`}
      </span>
      <span className="status-chips">
        <Chip label={aiLabel} ok={demo || status.ai_configured} />
        <Chip
          label="Razorpay Test Mode"
          ok={demo || (status.razorpay_configured && status.razorpay_test_mode)}
        />
        <Chip label="Webhook" ok={demo || status.webhook_configured} />
      </span>
      {notReady && (
        <span className="warn" role="alert">
          Live recovery is unavailable until configured
          {!status.ai_configured && ` — ${providerLabel(status.ai_provider)} API key`}
          {!status.razorpay_configured && " · Razorpay test key/secret"}
          {status.razorpay_configured && !status.razorpay_test_mode && " · key is not Test Mode"}.
          Running a case returns 503; set REVGUARD_MODE=demo for rehearsals.
        </span>
      )}
    </div>
  );
}

// Back-compat export (older imports / tests may reference StatusBanner).
export const StatusBanner = StatusStrip;

function Chip({ label, ok }: { label: string; ok: boolean }) {
  return <span className={`status-chip ${ok ? "ok" : "off"}`}>{label}</span>;
}
