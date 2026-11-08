from collections import deque
from collections.abc import Iterable

from ..application.integration_ports import DeliveryStatus, ReceivedMessage
from ..domain.integration import CorrelationEvidence


class InMemoryMessageReceiver:
    def __init__(self, messages: Iterable[ReceivedMessage] = ()) -> None:
        self._messages = deque(messages)

    def receive(self) -> ReceivedMessage | None:
        return self._messages.popleft() if self._messages else None


class RecordingEvidenceDelivery:
    def __init__(self, status: DeliveryStatus = DeliveryStatus.ACCEPTED) -> None:
        self.status = status
        self.deliveries: list[CorrelationEvidence] = []

    def deliver(self, evidence: CorrelationEvidence) -> DeliveryStatus:
        self.deliveries.append(evidence)
        return self.status


class RecordingSettlement:
    def __init__(self) -> None:
        self.completed: list[ReceivedMessage] = []
        self.abandoned: list[ReceivedMessage] = []
        self.dead_lettered: list[tuple[ReceivedMessage, str, tuple[str, ...]]] = []

    def complete(self, message: ReceivedMessage) -> None:
        self.completed.append(message)

    def abandon(self, message: ReceivedMessage) -> None:
        self.abandoned.append(message)

    def dead_letter(
        self, message: ReceivedMessage, reason: str, field_paths: tuple[str, ...]
    ) -> None:
        self.dead_lettered.append((message, reason, field_paths))


class RecordingObserver:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, str, str | None, float]] = []

    def record(
        self,
        outcome: str,
        message_id: str,
        correlation_id: str,
        reason: str | None,
        duration_seconds: float,
    ) -> None:
        self.records.append(
            (outcome, message_id, correlation_id, reason, duration_seconds)
        )

