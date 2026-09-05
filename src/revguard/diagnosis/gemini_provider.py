"""Gemini provider behind the :class:`Diagnoser` interface.

This module mirrors the fail-closed design of the Anthropic provider while using the
official ``google-genai`` SDK. Any malformed/absent model output or provider error
returns a safe escalation recommendation instead of an executable action.
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
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout_s = timeout_s
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from google import genai
            except Exception as exc:  # pragma: no cover - import-time dependency guard
                raise RuntimeError("google-genai SDK is not installed") from exc
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def diagnose(self, case: RecoveryCase) -> ActionProposal:
        try:
            client = self._get_client()
            response = client.models.generate_content(
                model=self._model,
                contents=build_user_prompt(case),
                config=_generate_config(
                    self._temperature, self._max_tokens, GEMINI_SYSTEM_PROMPT
                ),
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
            return safe_fallback_proposal(
                case, reason=user_message(kind), failure_kind=kind.value
            )

        draft = _extract_json_payload(response)
        if draft is None:
            logger.warning(
                "AI returned no structured proposal for case %s; escalating", case.case_id
            )
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
            return safe_fallback_proposal(
                case,
                reason=user_message(AIFailureKind.MALFORMED),
                failure_kind=AIFailureKind.MALFORMED.value,
            )

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
