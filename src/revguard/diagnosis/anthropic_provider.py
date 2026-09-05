"""Anthropic Claude provider behind the :class:`Diagnoser` interface (AGENT_SPEC §4, §6).

Uses schema-constrained tool output: the model must call the ``submit_action_proposal``
tool, whose input is validated against :class:`ActionProposal`. Any non-conforming or
missing output — or any provider error/timeout — degrades safely to
:func:`safe_fallback_proposal` (RECOMMEND_ESCALATION), so a bad/absent AI response can never
reach the executor.

The ``anthropic`` SDK is imported lazily (only when a real client is needed), so importing
this module — and running the whole app on ``MockDiagnoser`` — never requires the SDK. For
tests, a client can be injected; no real API calls are made.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from revguard.diagnosis.diagnoser import Diagnoser, safe_fallback_proposal
from revguard.diagnosis.prompts import (
    SUBMIT_TOOL_NAME,
    SYSTEM_PROMPT,
    build_user_prompt,
    proposal_tool_schema,
)
from revguard.domain import ActionProposal, RecoveryCase

logger = logging.getLogger(__name__)


class AnthropicProvider(Diagnoser):
    """Claude-backed diagnoser. Fails closed to an escalation recommendation."""

    name = "anthropic"

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
        self._client = client  # injectable for tests; real client created lazily

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic  # lazy: offline mode never needs the SDK

            self._client = anthropic.Anthropic(
                api_key=self._api_key, timeout=self._timeout_s
            )
        return self._client

    def diagnose(self, case: RecoveryCase) -> ActionProposal:
        try:
            client = self._get_client()
            tool = proposal_tool_schema()
            response = client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                system=SYSTEM_PROMPT,
                tools=[tool],
                tool_choice={"type": "tool", "name": SUBMIT_TOOL_NAME},
                messages=[{"role": "user", "content": build_user_prompt(case)}],
            )
        except Exception as exc:  # network/timeout/SDK/auth — never leak details
            logger.warning(
                "AI diagnosis call failed for case %s (%s); escalating",
                case.case_id,
                type(exc).__name__,
            )
            return safe_fallback_proposal(
                case, reason="AI provider unavailable; escalating for human review"
            )

        draft = _extract_tool_input(response, SUBMIT_TOOL_NAME)
        if draft is None:
            logger.warning(
                "AI returned no structured proposal for case %s; escalating", case.case_id
            )
            return safe_fallback_proposal(
                case, reason="AI returned no structured proposal; escalating"
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
                case, reason="AI proposal was malformed; escalating for human review"
            )

        logger.info(
            "AI diagnosis complete: case=%s provider=%s action=%s confidence=%s",
            case.case_id,
            self.name,
            proposal.action_type.value,
            proposal.confidence,
        )
        return proposal


def _extract_tool_input(response: Any, tool_name: str) -> dict[str, Any] | None:
    """Pull the tool-use input dict from a Messages response (dict- or object-shaped)."""
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    for block in content or []:
        btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if btype != "tool_use":
            continue
        bname = block.get("name") if isinstance(block, dict) else getattr(block, "name", None)
        binput = (
            block.get("input") if isinstance(block, dict) else getattr(block, "input", None)
        )
        if bname == tool_name and isinstance(binput, dict):
            return binput
    return None


def _draft_to_proposal(case: RecoveryCase, draft: dict[str, Any]) -> ActionProposal:
    """Build an ActionProposal from the tool draft. Pydantic rejects anything malformed."""
    return ActionProposal(
        case_id=case.case_id,  # server-controlled; never taken from the model
        action_type=draft["action_type"],
        rationale=draft["rationale"],
        confidence=draft["confidence"],
        parameters=draft.get("parameters") or {},
        evidence={"provider": "anthropic"},
        expected_recovery_amount=draft.get("expected_recovery_amount"),
        expected_recovery_currency=draft.get("expected_recovery_currency"),
    )


__all__ = ["AnthropicProvider"]
