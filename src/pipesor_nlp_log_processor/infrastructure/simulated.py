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

