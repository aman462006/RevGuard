"""Feed demo events and run one real diagnosis, end to end.

Usage (with the API already running, e.g. in production mode):

    py scripts/demo_diagnose.py                       # against localhost:8000
    py scripts/demo_diagnose.py --api http://...      # another deployment

What it does:
  1. Feeds the first ~80 synthetic events into the API (creates cases) -
     a no-op if the cases already exist (event ids deduplicate server-side).
  2. Picks the oldest WAITING case and POSTs /cases/{id}/run, which triggers
     the configured AI provider (Groq in production mode).
  3. Prints the diagnosis that came back.

If the server process was started with AFREC_API_URL set and the afrec extra
installed, step 2 streams a recorded run to the Agent Flight Recorder API as
it happens - open the dashboard and watch it land.

This script only speaks HTTP to the API; it never imports the agent.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def _post(url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload or {}, default=str).encode()  # events carry datetimes
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        return json.load(urllib.request.urlopen(req, timeout=90))
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:400]
        raise SystemExit(f"POST {url} -> {e.code}: {body}")


def _get(url: str) -> dict:
    try:
        return json.load(urllib.request.urlopen(url, timeout=60))
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:400]
        raise SystemExit(f"GET {url} -> {e.code}: {body}")


def _feed_events(api: str) -> int:
    """POST the first 80 synthetic events; server dedupes re-feeds."""
    try:
        from revguard.synthetic import DEFAULT_SEED
        from revguard.synthetic.generator import generate_events
    except ImportError:
        raise SystemExit(
            "revguard is not importable in this python. Run from the repo root "
            "with `py -m pip install -e .` done first."
        )
    events = generate_events(DEFAULT_SEED)
    accepted = 0
    for ev in events:
        d = ev.model_dump() if hasattr(ev, "model_dump") else vars(ev)
        d.pop("event_id", None)
        _post(f"{api}/events", d)
        accepted += 1
    return accepted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://localhost:8000", help="RevGuard API base URL")
    args = parser.parse_args()
    api = args.api.rstrip("/")

    status = _get(f"{api}/status")
    mode = status.get("mode", "?")
    print(f"API mode: {mode}")
    if mode != "production":
        print("NOTE: API is NOT in production mode - the diagnosis will be the "
              "offline mock, and afrec will record nothing real.")

    print("Feeding demo events (idempotent)…")
    fed = _feed_events(api)
    print(f"  {fed} events accepted (re-feeds deduplicate server-side)")

    cases = _get(f"{api}/cases")
    case_list = cases.get("cases", cases) if isinstance(cases, dict) else cases
    open_cases = [c for c in case_list if not c.get("is_terminal") and c.get("status") == "detected"]
    if not open_cases:
        open_cases = [c for c in case_list if not c.get("is_terminal")]
    if not open_cases:
        print("No open cases after feeding; check the Cases tab.")
        return
    case = open_cases[0]
    case_id = case.get("case_id") or case.get("id")
    print(f"Running diagnosis on case {case_id} ({case.get('case_type', '')}, risk {case.get('risk_level', '')})…")

    result = _post(f"{api}/cases/{case_id}/run")
    diag = result.get("diagnosis") or result
    print("\nDIAGNOSIS:")
    print(json.dumps(diag, indent=1, default=str)[:1200])
    print("\nDone. If AFREC_API_URL was set on the server, the run is now on the "
          "Agent Flight Recorder dashboard with real token counts.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        print(e, file=sys.stderr)
        sys.exit(1)
