"""Groq provider (Llama 3.3 / Qwen) behind the :class:`Diagnoser` interface.

Every diagnosis is recorded through afrec (Agent Flight Recorder), exactly
like the Gemini path: one flight per diagnosis, real token usage from the
provider response, and a diagnosis-outcome span for every exit path. A
missing afrec install degrades to a no-op recorder and changes nothing.

Mirrors the fail-closed design of the other providers using the official ``groq`` SDK, which
is OpenAI-compatible. The model is driven in native JSON mode
(``response_format={"type": "json_object"}``) and asked for a single JSON object; any malformed
or absent output, or any provider error, returns a safe escalation recommendation instead of an
executable action. The deterministic policy engine remains the sole authority on execution.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from revguard.diagnosis.diagnoser import Diagnoser, safe_fallback_proposal
from revguard.diagnosis.errors import AIFailureKind, classify_exception, user_message
from revguard.diagnosis.gemini_provider import _NullRecorder
from revguard.diagnosis.prompts import ALLOWED_ACTION_VALUES, build_user_prompt
from revguard.domain import ActionProposal, RecoveryCase

logger = logging.getLogger(__name__)

# Groq/Llama system instruction. Like the Gemini path, we ask for a single raw JSON object
# (no tool/function calls) and steer strictly to the allowed action enum. The word "JSON" must
# appear in the prompt for the OpenAI-compatible ``json_object`` response format.
GROQ_SYSTEM_PROMPT = (
    "You are RevGuard's revenue-recovery diagnosis assistant. Recommend exactly ONE recovery "
    "action for the given case. You are a reasoning/recommendation component — you do not and "
    "cannot execute anything.\n\n"
    "Respond with ONLY a single JSON object (no markdown fences, no prose) with these keys:\n"
    '  "action_type": one of [' + ", ".join(ALLOWED_ACTION_VALUES) + "]\n"
    '  "rationale": a concise string grounded solely in the supplied evidence\n'
    '  "confidence": a number between 0 and 1\n\n'
    "Rules you MUST follow:\n"
    "- Choose action_type strictly from the allowed list. Never invent an action.\n"
    "- Never claim an action was performed. You only recommend.\n"
    "- If you are uncertain, prefer wait or recommend_escalation.\n"
    "- Do not reason about or try to override policy limits — a separate deterministic policy "
    "engine decides whether your recommendation is approved, escalated, or stopped."
)


class GroqProvider(Diagnoser):
    """Groq-backed (Llama 3.3) diagnoser. Fails closed to an escalation recommendation."""

    name = "groq"

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        timeout_s: float = 30.0,
        client: Any | None = None,
        recorder: Any | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout_s = timeout_s
        self._client = client
        self._recorder = recorder

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from groq import Groq
            except Exception as exc:  # pragma: no cover - import-time dependency guard
                raise RuntimeError("groq SDK is not installed") from exc
            self._client = Groq(api_key=self._api_key, timeout=self._timeout_s)
        return self._client

    def _get_recorder(self) -> Any:
        """Explicit recorder wins (tests); else afrec's process default; else no-op.

        Never raises: observability is optional for this module by design.
        """
        if self._recorder is not None:
            return self._recorder
        try:
            from afrec import get_recorder

            return get_recorder()
        except Exception:  # afrec not installed — run unrecorded
            return _NullRecorder()

    def diagnose(self, case: RecoveryCase) -> ActionProposal:
        # One recorded flight per diagnosis, mirroring the Gemini path.
        rec = self._get_recorder()
        with rec.run(
            f"Diagnose recovery case {case.case_id}",
            tags={"agent": "revguard", "provider": self.name, "model": self._model},
        ) as run:
            return self._diagnose(case, run)

    def _diagnose(self, case: RecoveryCase, run: Any) -> ActionProposal:
        try:
            client = self._get_client()
            prompt = build_user_prompt(case)
            messages = [
                {"role": "system", "content": GROQ_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            with run.llm_call(self._model, messages=messages) as span:
                response = client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    response_format={"type": "json_object"},
                )
                draft = _extract_json_payload(response)
                usage = getattr(response, "usage", None)
                span.result(
                    {"proposal": draft} if draft is not None else {"raw": str(response)[:2000]},
                    input_tokens=getattr(usage, "prompt_tokens", None),
                    output_tokens=getattr(usage, "completion_tokens", None),
                    model=self._model,
                )
        except Exception as exc:  # network/timeout/SDK/auth/quota — classify, never leak details
            kind = classify_exception(exc)
            # Log the failure classification + exception TYPE only — never the message, which
            # can contain request details or credentials.
            logger.warning(
                "AI diagnosis call failed for case %s (kind=%s, type=%s); escalating",
                case.case_id,
                kind.value,
                type(exc).__name__,
            )
            with run.span("diagnosis-outcome", failure_kind=kind.value, escalated=True):
                pass
            return safe_fallback_proposal(
                case, reason=user_message(kind), failure_kind=kind.value
            )

        if draft is None:
            logger.warning(
                "AI returned no structured proposal for case %s; escalating", case.case_id
            )
            with run.span(
                "diagnosis-outcome",
                failure_kind=AIFailureKind.BAD_RESPONSE.value,
                escalated=True,
            ):
                pass
            return safe_fallback_proposal(
                case,
                reason=user_message(AIFailureKind.BAD_RESPONSE),
                failure_kind=AIFailureKind.BAD_RESPONSE.value,
            )

        try:
            proposal = _draft_to_proposal(case, draft)
        except (ValidationError, KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "AI proposal for case %s was malformed (%s); escalating",
                case.case_id,
                type(exc).__name__,
            )
            with run.span(
                "diagnosis-outcome",
                failure_kind=AIFailureKind.MALFORMED.value,
                escalated=True,
            ):
                pass
            return safe_fallback_proposal(
                case,
                reason=user_message(AIFailureKind.MALFORMED),
                failure_kind=AIFailureKind.MALFORMED.value,
            )

        with run.span(
            "diagnosis-outcome",
            action_type=proposal.action_type.value,
            confidence=proposal.confidence,
            escalated=False,
        ):
            pass
        logger.info(
            "AI diagnosis complete: case=%s provider=%s action=%s confidence=%s",
            case.case_id,
            self.name,
            proposal.action_type.value,
            proposal.confidence,
        )
        return proposal


def _extract_text(response: Any) -> str | None:
    """Pull the assistant message text from a Groq/OpenAI-shaped chat completion."""
    if response is None:
        return None
    if isinstance(response, str):
        return response
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices")
    if not choices:
        return None
    first = choices[0]
    message = getattr(first, "message", None)
    if message is None and isinstance(first, dict):
        message = first.get("message")
    if message is None:
        return None
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return content if isinstance(content, str) else None


def _extract_json_payload(response: Any) -> dict[str, Any] | None:
    """Pull the first JSON object from a Groq chat completion payload."""
    text = _extract_text(response)
    if text is None:
        return None

    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3:
            candidate = "\n".join(lines[1:-1])
        else:
            candidate = candidate.replace("```", "").strip()

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        parsed = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


def _draft_to_proposal(case: RecoveryCase, draft: dict[str, Any]) -> ActionProposal:
    """Build an ActionProposal from the JSON draft. Pydantic rejects malformed values."""
    return ActionProposal(
        case_id=case.case_id,
        action_type=draft["action_type"],
        rationale=draft["rationale"],
        confidence=draft["confidence"],
        parameters=draft.get("parameters") or {},
        evidence={"provider": "groq"},
        expected_recovery_amount=draft.get("expected_recovery_amount"),
        expected_recovery_currency=draft.get("expected_recovery_currency"),
    )


__all__ = ["GroqProvider"]
