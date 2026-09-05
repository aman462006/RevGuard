"""ActionAdapter interface + AdapterResult (Phase 6).

An adapter performs a **single whitelisted business action** and reports a *technical*
outcome. It deliberately exposes one method per permitted :class:`ActionType` rather than a
generic ``execute(name, args)`` — there is no way to ask an adapter to run an arbitrary
tool, function, URL, or piece of code. :meth:`ActionAdapter.perform` is the only dispatch
entry point, and it maps over a closed action → method table; anything outside it raises
:class:`UnsupportedActionError`.

An :class:`AdapterResult` reports only what happened *technically* (accepted / ran /
failed) plus a simulated provider reference. It carries ``simulated`` explicitly and
**never** asserts that money was recovered — recovery is verified separately in Phase 7.
Adapters perform no policy checks; the executor guarantees an APPROVE before calling one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from revguard.domain import ActionProposal, ActionType, RecoveryCase
from revguard.execution.errors import UnsupportedActionError

# The closed set of actions any adapter may perform: the side-effecting recovery actions
# plus WAIT (which schedules a delay and causes no external side effect).
ADAPTER_ACTIONS: frozenset[ActionType] = frozenset(
    {
        ActionType.RETRY_PAYMENT,
        ActionType.CREATE_PAYMENT_LINK,
        ActionType.SEND_REMINDER,
        ActionType.RECORD_PROMISE_TO_PAY,
        ActionType.WAIT,
    }
)

# action → adapter method name. This closed table is the ONLY way an ActionType is turned
# into a call, so an adapter can never be driven to run something outside the whitelist.
_ACTION_METHODS: dict[ActionType, str] = {
    ActionType.RETRY_PAYMENT: "retry_payment",
    ActionType.CREATE_PAYMENT_LINK: "create_payment_link",
    ActionType.SEND_REMINDER: "send_reminder",
    ActionType.RECORD_PROMISE_TO_PAY: "record_promise_to_pay",
    ActionType.WAIT: "wait",
}


@dataclass(frozen=True)
class AdapterResult:
    """The *technical* outcome of performing one action. NOT a recovery claim.

    ``succeeded`` means the action ran to completion technically — it says nothing about
    whether money was recovered (that is verification's concern, Phase 7). ``simulated``
    marks results produced without touching a real payment provider.
    """

    action: ActionType
    accepted: bool
    succeeded: bool
    detail: str
    reference: str | None = None
    # A customer-facing provider URL, when the action produces one (e.g. a Razorpay Payment
    # Link ``short_url``). Purely informational — its existence is NOT a recovery claim.
    url: str | None = None
    failure_reason: str | None = None
    simulated: bool = True


class ActionAdapter(ABC):
    """Performs one whitelisted action at a time. Exposes no arbitrary execution."""

    name: str = "adapter"

    @abstractmethod
    def retry_payment(self, case: RecoveryCase, proposal: ActionProposal) -> AdapterResult: ...

    @abstractmethod
    def create_payment_link(
        self, case: RecoveryCase, proposal: ActionProposal
    ) -> AdapterResult: ...

    @abstractmethod
    def send_reminder(self, case: RecoveryCase, proposal: ActionProposal) -> AdapterResult: ...

    @abstractmethod
    def record_promise_to_pay(
        self, case: RecoveryCase, proposal: ActionProposal
    ) -> AdapterResult: ...

    @abstractmethod
    def wait(self, case: RecoveryCase, proposal: ActionProposal) -> AdapterResult: ...

    def perform(
        self, action: ActionType, case: RecoveryCase, proposal: ActionProposal
    ) -> AdapterResult:
        """Dispatch to the handler for ``action`` over the closed whitelist.

        Raises :class:`UnsupportedActionError` for any action the adapter cannot perform —
        there is no generic/dynamic execution path.
        """
        method_name = _ACTION_METHODS.get(action)
        if method_name is None:
            raise UnsupportedActionError(
                f"adapter {self.name!r} cannot perform action {action.value!r}"
            )
        return getattr(self, method_name)(case, proposal)


__all__ = ["ActionAdapter", "AdapterResult", "ADAPTER_ACTIONS"]
