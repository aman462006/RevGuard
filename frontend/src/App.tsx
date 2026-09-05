import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import type { BatchMetrics, CaseSummary, StatusInfo } from "./types";
import { About } from "./components/About";
import { Analytics } from "./components/Analytics";
import { CaseDetailPanel } from "./components/CaseDetail";
import { CasesTable } from "./components/CasesTable";
import { DemoControls } from "./components/DemoControls";
import { Evaluation } from "./components/Evaluation";
import { Overview } from "./components/Overview";
import { StatusStrip } from "./components/StatusBanner";

type Tab = "about" | "overview" | "cases" | "analytics" | "evaluation" | "demo";

const TABS: { key: Tab; label: string }[] = [
  { key: "about", label: "About" },
  { key: "demo", label: "Demo" },
  { key: "cases", label: "Cases" },
  { key: "overview", label: "Overview" },
  { key: "analytics", label: "Analytics" },
  { key: "evaluation", label: "Evaluation" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("about");
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [metrics, setMetrics] = useState<BatchMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [status, setStatus] = useState<StatusInfo | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [list, m] = await Promise.all([api.listCases(), api.metrics()]);
      setCases(list.cases);
      setMetrics(m);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load data.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    // Mode/readiness is fetched once; it is independent of case data.
    api.status().then(setStatus).catch(() => setStatus(null));
  }, [refresh]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">RevGuard</span>
          <span className="tagline">AI Revenue Recovery Agent</span>
        </div>
        <nav className="tabs">
          {TABS.map((t) => (
            <button
              key={t.key}
              className={`tab ${tab === t.key ? "active" : ""}`}
              aria-current={tab === t.key ? "page" : undefined}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      <StatusStrip status={status} />

      <main className="content">
        {tab === "overview" && (
          <Overview
            metrics={metrics}
            loading={loading}
            error={error}
            onRetry={refresh}
            cases={cases}
            onSelect={setSelected}
          />
        )}
        {tab === "cases" && (
          <CasesTable
            cases={cases}
            loading={loading}
            error={error}
            onRetry={refresh}
            onSelect={setSelected}
            selectedId={selected}
          />
        )}
        {tab === "analytics" && <Analytics />}
        {tab === "evaluation" && <Evaluation />}
        {tab === "demo" && (
          <DemoControls
            onCreated={refresh}
            onDone={() => setTab("overview")}
            status={status}
          />
        )}
        {tab === "about" && (
          <About onEnter={() => setTab("overview")} onDemo={() => setTab("demo")} />
        )}
      </main>

      {selected && (
        <>
          <div className="scrim" onClick={() => setSelected(null)} />
          <CaseDetailPanel
            caseId={selected}
            status={status}
            onClose={() => setSelected(null)}
            onChanged={refresh}
          />
        </>
      )}

      <footer className="footer">
        <span className="mono">{api.baseUrl}</span> — The AI recommends; a deterministic
        PolicyEngine authorizes execution; recovery is confirmed only by verification.
      </footer>
    </div>
  );
}
