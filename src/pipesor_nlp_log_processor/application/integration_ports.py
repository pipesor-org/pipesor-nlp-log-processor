from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ..domain.integration import CorrelationEvidence


@dataclass(frozen=True, slots=True)
class ReceivedMessage:
    body: bytes
    message_id: str
    correlation_id: str


class DeliveryStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"


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

