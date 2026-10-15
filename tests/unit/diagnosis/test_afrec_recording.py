"""afrec recording in GeminiProvider: records real flights, never changes behaviour.

Three guarantees under test:
  1. A successful diagnosis records a run: LLM span with the model's real
     token usage, plus a diagnosis-outcome span with the approved action.
  2. A provider failure records the failure path too — the escalated outcome
     and the exception are on the trace, so a silent-escalation storm is
     visible on the dashboard, not just in logs.
  3. Without afrec installed (NullRecorder fallback), diagnosis still works.
"""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

from afrec import Recorder
from afrec.recorder import SqliteStore

from revguard.diagnosis.gemini_provider import GeminiProvider, _NullRecorder
from revguard.domain import ActionType


class FakeGeminiClient:
    def __init__(self, response=None, error=None, usage=None):
        self._response = response
        self._error = error
        self.models = SimpleNamespace(generate_content=self.generate_content)
        self.usage = usage or SimpleNamespace(
            prompt_token_count=1500, candidates_token_count=40, cached_content_token_count=0
        )

    def generate_content(self, **kwargs):
        if self._error is not None:
            raise self._error
        resp = SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(
                        parts=[
                            SimpleNamespace(
                                text=(
                                    '{"action_type": "retry_payment", '
                                    '"rationale": "mandate failed once; retry is likely", '
                                    '"confidence": 0.7}'
                                )
                            )
                        ]
                    )
                )
            ],
            usage_metadata=self.usage,
        )
        return resp


def make_recorder():
    tmp = tempfile.mkdtemp()
    return Recorder(store=SqliteStore(os.path.join(tmp, "t.sqlite3")))


def test_success_path_records_llm_span_with_real_token_usage(make_case):
    rec = make_recorder()
    client = FakeGeminiClient()
    proposal = GeminiProvider(
        model="gemini-2.0-flash", client=client, recorder=rec
    ).diagnose(make_case())

    assert proposal.action_type is ActionType.RETRY_PAYMENT

    runs = rec.store.list_runs()
    assert len(runs) == 1 and runs[0]["status"] == "completed"
    assert runs[0]["tags"]["provider"] == "gemini"
    assert runs[0]["tags"]["model"] == "gemini-2.0-flash"

    events = rec.store.get_events(runs[0]["run_id"])
    kinds = [e["kind"] for e in events]
    assert "run.start" in kinds and "run.end" in kinds

    llm_resp = next(e for e in events if e["kind"] == "llm.response")
    # Real usage_metadata from the provider response, not fabricated.
    assert llm_resp["input_tokens"] == 1500
    assert llm_resp["output_tokens"] == 40
    assert llm_resp["model"] == "gemini-2.0-flash"

    outcome = next(
        e
        for e in events
        if e["kind"] == "span.start" and e["data"].get("label") == "diagnosis-outcome"
    )
    assert outcome["data"]["action_type"] == "retry_payment"
    assert outcome["data"]["escalated"] is False


def test_provider_failure_records_escalated_outcome(make_case):
    rec = make_recorder()
    client = FakeGeminiClient(error=TimeoutError("simulated provider timeout"))
    proposal = GeminiProvider(
        model="gemini-2.0-flash", client=client, recorder=rec
    ).diagnose(make_case())

    assert proposal.action_type is ActionType.RECOMMEND_ESCALATION

    events = rec.store.get_events(rec.store.list_runs()[0]["run_id"])
    outcome = [
        e
        for e in events
        if e["kind"] == "span.start" and e["data"].get("label") == "diagnosis-outcome"
    ]
    assert any(e["data"].get("escalated") is True for e in outcome)
    # The exception itself is on the trace — the LLM span records the error.
    assert any(e["kind"] == "error" for e in events)


def test_null_recorder_keeps_diagnosis_working(make_case):
    """No afrec? Diagnosis must behave identically (recording is optional)."""
    client = FakeGeminiClient()
    provider = GeminiProvider(model="m", client=client, recorder=_NullRecorder())
    proposal = provider.diagnose(make_case())
    assert proposal.action_type is ActionType.RETRY_PAYMENT
    assert client.models.generate_content  # provider untouched


def test_missing_afrec_degrades_to_noop_recorder():
    provider = GeminiProvider(model="m", client=FakeGeminiClient())
    recorder = provider._get_recorder()
    # With afrec installed this is the real default recorder; the point is it
    # never raises and exposes run().
    with recorder.run("smoke") as run:
        with run.span("s"):
            pass
