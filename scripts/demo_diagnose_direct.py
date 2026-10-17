r"""Run ONE real Groq diagnosis directly, recorded to the AFREC dashboard.

The API's POST /cases/{id}/run runs the FULL pipeline and requires the
Razorpay recovery integration in production mode. This script runs only the
AI diagnosis step - the real Groq Llama 3.3 call - against a case from the
local database, with afrec recording enabled. No Razorpay keys needed.

Run from the repo root:

    powershell -ExecutionPolicy Bypass -File scripts/demo_diagnose_direct.ps1
"""

from __future__ import annotations

import json
import sys

from revguard.config import Settings
from revguard.diagnosis.groq_provider import GroqProvider
from revguard.persistence import CaseRepository, Database


def main() -> None:
    settings = Settings()
    if not settings.groq_api_key:
        raise SystemExit("GROQ_API_KEY is not set in this process environment.")
    if not (settings.afrec_api_url if hasattr(settings, "afrec_api_url") else None):
        # afrec reads AFREC_API_URL from os.environ directly; just remind.
        import os
        if not os.environ.get("AFREC_API_URL"):
            print("WARNING: AFREC_API_URL is not set - the run will only buffer locally.")
    print(f"model: {settings.ai_model}")

    db = Database(settings.database_url)
    with db.session() as session:
        rows = CaseRepository(session)
        all_cases = rows.list_all() if hasattr(rows, "list_all") else []
        case = all_cases[0] if all_cases else None
        if case is None:
            raise SystemExit("No cases in the local database - run demo_diagnose.py first "
                             "to feed events, then rerun this script.")
    print(f"case: {case.case_id} ({case.case_type.value if hasattr(case.case_type, 'value') else case.case_type})")

    provider = GroqProvider(
        model=settings.ai_model,
        api_key=settings.groq_api_key,
        temperature=settings.ai_temperature,
        max_tokens=settings.ai_max_tokens,
        timeout_s=settings.ai_timeout_s,
    )
    proposal = provider.diagnose(case)

    print("\nDIAGNOSIS PROPOSAL:")
    print(json.dumps(proposal.model_dump() if hasattr(proposal, "model_dump") else vars(proposal),
                     indent=1, default=str)[:1000])
    print("\nRecorded to the AFREC dashboard with real token counts.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        print(e, file=sys.stderr)
        sys.exit(1)
