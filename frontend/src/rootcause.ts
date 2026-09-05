// Parses the payment-degradation root-cause breakdown from a case's signal evidence.
// Pure and defensive: unknown/missing shapes yield null (the panel simply doesn't render),
// and unavailable dimensions are represented explicitly (never invented).

export interface Segment {
  value: string;
  failures: number;
  amount: string;
}

export interface Dimension {
  key: "method" | "issuer" | "error_code";
  label: string;
  available: boolean;
  dominant: string | null;
  dominantShare: number | null;
  coverage: number | null;
  segments: Segment[];
}

export interface RootCause {
  method: string;
  totalFailures: number;
  observedFailureRate: number | null;
  baselineFailureRate: number | null;
  primaryDimension: string | null;
  primarySegment: string | null;
  primaryShare: number | null;
  summary: string | null;
  mitigation: string | null;
  dimensions: Dimension[];
}

const LABELS: Record<Dimension["key"], string> = {
  method: "Payment method",
  issuer: "Issuer / bank",
  error_code: "Error code",
};

function num(v: unknown): number | null {
  return typeof v === "number" ? v : null;
}
function str(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}
function rec(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function parseDimension(key: Dimension["key"], raw: unknown): Dimension {
  const d = rec(raw);
  if (!d || d.available !== true) {
    return {
      key,
      label: LABELS[key],
      available: false,
      dominant: null,
      dominantShare: null,
      coverage: null,
      segments: [],
    };
  }
  const segsRaw = rec(d.segments) ?? {};
  const segments: Segment[] = Object.entries(segsRaw)
    .map(([value, s]) => {
      const sr = rec(s) ?? {};
      return { value, failures: num(sr.failures) ?? 0, amount: str(sr.amount) ?? "0" };
    })
    .sort((a, b) => b.failures - a.failures);
  return {
    key,
    label: LABELS[key],
    available: true,
    dominant: str(d.dominant),
    dominantShare: num(d.dominant_share),
    coverage: num(d.coverage),
    segments,
  };
}

export function parseRootCause(evidence: Record<string, unknown> | undefined): RootCause | null {
  if (!evidence) return null;
  const rc = rec(evidence.root_cause);
  if (!rc) return null;
  const dims = rec(rc.dimensions);
  if (!dims) return null;
  const primary = rec(rc.primary) ?? {};
  return {
    method: str(rc.affected_method) ?? str(evidence.method) ?? "unknown",
    totalFailures: num(rc.total_failures) ?? 0,
    observedFailureRate: num(evidence.observed_failure_rate),
    baselineFailureRate: num(evidence.baseline_failure_rate),
    primaryDimension: str(primary.dimension),
    primarySegment: str(primary.segment),
    primaryShare: num(primary.share),
    summary: str(rc.summary),
    mitigation: str(rc.suggested_mitigation),
    dimensions: [
      parseDimension("method", dims.method),
      parseDimension("issuer", dims.issuer),
      parseDimension("error_code", dims.error_code),
    ],
  };
}
