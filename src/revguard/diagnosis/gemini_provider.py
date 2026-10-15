"""Gemini provider behind the :class:`Diagnoser` interface.

This module mirrors the fail-closed design of the Anthropic provider while using the
official ``google-genai`` SDK. Any malformed/absent model output or provider error
returns a safe escalation recommendation instead of an executable action.

Every diagnosis is recorded through afrec (Agent Flight Recorder) when it is
installed — a run per case, an LLM span with the real token usage, and a
final outcome span. Observability is deliberately optional and fail-open in
exactly one direction: recording must never change what this provider
returns, and a missing afrec install degrades to a no-op recorder.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from revguard.diagnosis.diagnoser import Diagnoser, safe_fallback_proposal
from revguard.diagnosis.errors import AIFailureKind, classify_exception, user_message
from revguard.diagnosis.prompts import ALLOWED_ACTION_VALUES, build_user_prompt
from revguard.domain import ActionProposal, RecoveryCase

logger = logging.getLogger(__name__)


class _NoopSpan:
    """Context-manager stand-in; swallows result() without doing anything."""

    def __enter__(self) -> _NoopSpan:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def result(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401 — no-op by design
        """Discard; recording is disabled (afrec not installed)."""


class _NoopRun:
    """Mirrors the slice of afrec's Run API this module touches."""

    def __enter__(self) -> _NoopRun:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def span(self, label: str, **data: Any) -> _NoopSpan:
        return _NoopSpan()

    def llm_call(self, model: str, messages: Any) -> _NoopSpan:
        return _NoopSpan()

    def tool_call(self, name: str, arguments: Any) -> _NoopSpan:
        return _NoopSpan()


class _NullRecorder:
    """Fallback when afrec is not installed: the provider runs unrecorded."""

    def run(self, task: str, tags: Any = None) -> _NoopRun:
        return _NoopRun()


# Gemini-specific system instruction. Unlike Anthropic (which we drive with a schema-constrained
# *tool*), the google-genai path uses native JSON output (``response_mime_type=application/json``)
# and asks for a plain JSON object. Instructing Gemini 3.x to "call a tool" that is not declared
# makes it emit a MALFORMED_FUNCTION_CALL and return empty text, so we deliberately steer it to a
# JSON object here. The deterministic policy engine remains the sole authority on execution.
GEMINI_SYSTEM_PROMPT = (
    "You are RevGuard's revenue-recovery diagnosis assistant. Recommend exactly ONE recovery "
    "action for the given case. You are a reasoning/recommendation component — you do not and "
    "cannot execute anything.\n\n"
    "Respond with ONLY a single JSON object (no markdown fences, no prose, no tool/function "
    "calls) with these keys:\n"
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


class GeminiProvider(Diagnoser):
    """Gemini-backed diagnoser. Fails closed to an escalation recommendation."""

    name = "gemini"

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

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from google import genai
            except Exception as exc:  # pragma: no cover - import-time dependency guard
                raise RuntimeError("google-genai SDK is not installed") from exc
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def diagnose(self, case: RecoveryCase) -> ActionProposal:
        # One recorded flight per diagnosis. afrec's spans never raise, and a
        # missing install degrades to the no-op recorder — diagnosis semantics
        # are identical either way.
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
            with run.llm_call(
                self._model,
                messages=[
                    {"role": "system", "content": GEMINI_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            ) as span:
                response = client.models.generate_content(
                    model=self._model,
                    contents=prompt,
                    config=_generate_config(
                        self._temperature, self._max_tokens, GEMINI_SYSTEM_PROMPT
                    ),
                )
                draft = _extract_json_payload(response)
                usage = getattr(response, "usage_metadata", None)
                span.result(
                    {"proposal": draft}
                    if draft is not None
                    else {"text": _extract_text(response)},
                    input_tokens=getattr(usage, "prompt_token_count", None),
                    output_tokens=getattr(usage, "candidates_token_count", None),
                    cached_tokens=getattr(usage, "cached_content_token_count", None),
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


def _generate_config(temperature: float, max_tokens: int, system_prompt: str) -> Any:
    # ``response_mime_type=application/json`` makes Gemini return a raw JSON object (parsed by
    # ``_extract_json_payload``) rather than attempting a function/tool call — the latter yields
    # MALFORMED_FUNCTION_CALL + empty text on Gemini 3.x and forces a fail-closed escalation.
    try:
        from google.genai import types
    except Exception:
        return {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
            "system_instruction": system_prompt,
            "response_mime_type": "application/json",
        }

    return types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_tokens,
        system_instruction=system_prompt,
        response_mime_type="application/json",
    )


def _extract_json_payload(response: Any) -> dict[str, Any] | None:
    """Pull the first JSON object from a Gemini response payload."""
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

    payload = candidate[start : end + 1]
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


def _extract_text(response: Any) -> str | None:
    if response is None:
        return None
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        text = response.get("text")
        if isinstance(text, str):
            return text
        candidates = response.get("candidates")
        if isinstance(candidates, list):
            for candidate in candidates:
                value = _extract_text(candidate)
                if value is not None:
                    return value
        return None

    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text

    candidates = getattr(response, "candidates", None)
    if isinstance(candidates, list):
        for candidate in candidates:
            value = _extract_text(candidate)
            if value is not None:
                return value

    content = getattr(response, "content", None)
    if content is not None:
        value = _extract_text(content)
        if value is not None:
            return value

    parts = getattr(response, "parts", None)
    if isinstance(parts, list):
        for part in parts:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str):
                return part_text
            if isinstance(part_text, list):
                return "".join(str(item) for item in part_text)
    return None


def _draft_to_proposal(case: RecoveryCase, draft: dict[str, Any]) -> ActionProposal:
    """Build an ActionProposal from the JSON draft. Pydantic rejects malformed values."""
    return ActionProposal(
        case_id=case.case_id,
        action_type=draft["action_type"],
        rationale=draft["rationale"],
        confidence=draft["confidence"],
        parameters=draft.get("parameters") or {},
        evidence={"provider": "gemini"},
        expected_recovery_amount=draft.get("expected_recovery_amount"),
        expected_recovery_currency=draft.get("expected_recovery_currency"),
    )


__all__ = ["GeminiProvider"]
