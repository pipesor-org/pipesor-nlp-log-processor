from collections.abc import Callable
from enum import StrEnum

from .integration_ports import (
    MessageReceiver,
    MessageSettlement,
    PermanentDeliveryError,
    ProcessingObserver,
    TransientDeliveryError,
)
from .process_evidence import ProcessEvidence
from ..domain.integration import InvalidIntegrationMessage


class ProcessingOutcome(StrEnum):
    IDLE = "idle"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    DEAD_LETTERED = "dead_lettered"


class MessageLoop:
    def __init__(
        self,
        receiver: MessageReceiver,
        settlement: MessageSettlement,
        processor: ProcessEvidence,
        observer: ProcessingObserver,
    ) -> None:
        self._receiver = receiver
        self._settlement = settlement
        self._processor = processor
        self._observer = observer

    def process_next(self) -> ProcessingOutcome:
        message = self._receiver.receive()
        if message is None:
            return ProcessingOutcome.IDLE

        try:
            self._processor.execute(message)
        except InvalidIntegrationMessage as error:
            self._settlement.dead_letter(message, error.code, error.field_paths)
            self._observer.record(
                ProcessingOutcome.DEAD_LETTERED,
                message.message_id,
                message.correlation_id,
                error.code,
            )
            return ProcessingOutcome.DEAD_LETTERED
        except PermanentDeliveryError as error:
            self._settlement.dead_letter(message, error.code, ())
            self._observer.record(
                ProcessingOutcome.DEAD_LETTERED,
                message.message_id,
                message.correlation_id,
                error.code,
            )
            return ProcessingOutcome.DEAD_LETTERED
        except Exception as error:
            self._settlement.abandon(message)
            reason = error.code if isinstance(error, TransientDeliveryError) else "processing_failure"
            self._observer.record(
                ProcessingOutcome.ABANDONED,
                message.message_id,
                message.correlation_id,
                reason,
            )
            return ProcessingOutcome.ABANDONED

        self._settlement.complete(message)
        self._observer.record(
            ProcessingOutcome.COMPLETED,
            message.message_id,
            message.correlation_id,
            None,
        )
        return ProcessingOutcome.COMPLETED

    def run(self, stop_requested: Callable[[], bool]) -> None:
        while not stop_requested():
            self.process_next()

