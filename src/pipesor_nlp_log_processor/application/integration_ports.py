from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from ..domain.integration import CorrelationEvidence


@dataclass(frozen=True, slots=True)
class ReceivedMessage:
    body: bytes
    message_id: str
    correlation_id: str
    traceparent: str | None = None
    tracestate: str | None = None
    contract_error: str | None = None
    settlement_token: object | None = field(default=None, compare=False, repr=False)


class DeliveryStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"


class DeliveryError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class TransientDeliveryError(DeliveryError):
    """The handoff may succeed when the broker redelivers the message."""


class PermanentDeliveryError(DeliveryError):
    """The downstream contract rejected this message permanently."""


class EvidenceDelivery(Protocol):
    def deliver(self, evidence: CorrelationEvidence) -> DeliveryStatus: ...


class MessageReceiver(Protocol):
    def receive(self) -> ReceivedMessage | None: ...


class MessageSettlement(Protocol):
    def complete(self, message: ReceivedMessage) -> None: ...

    def abandon(self, message: ReceivedMessage) -> None: ...

    def dead_letter(
        self, message: ReceivedMessage, reason: str, field_paths: tuple[str, ...]
    ) -> None: ...


class ProcessingObserver(Protocol):
    def record(
        self, outcome: str, message_id: str, correlation_id: str, reason: str | None
    ) -> None: ...

