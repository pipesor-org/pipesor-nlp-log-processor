from collections.abc import Callable
from enum import StrEnum
import time

from .integration_ports import (
    AuthorizationDeliveryError,
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
    PAUSED = "paused"
    DUPLICATE = "duplicate"


class ConsumptionPaused(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class MessageLoop:
    def __init__(
        self,
        receiver: MessageReceiver,
        settlement: MessageSettlement,
        processor: ProcessEvidence,
        observer: ProcessingObserver,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._receiver = receiver
        self._settlement = settlement
        self._processor = processor
        self._observer = observer
        self._clock = clock

    def _record(
        self,
        outcome: ProcessingOutcome,
        message_id: str,
        correlation_id: str,
        reason: str | None,
        started_at: float,
    ) -> None:
        self._observer.record(
            outcome,
            message_id,
            correlation_id,
            reason,
            max(0.0, self._clock() - started_at),
        )

    def process_next(self) -> ProcessingOutcome:
        message = self._receiver.receive()
        if message is None:
            return ProcessingOutcome.IDLE
        started_at = self._clock()

        try:
            delivery_status = self._processor.execute(message)
        except AuthorizationDeliveryError as error:
            self._settlement.abandon(message)
            self._record(
                ProcessingOutcome.PAUSED,
                message.message_id,
                message.correlation_id,
                error.code,
                started_at,
            )
            raise ConsumptionPaused(error.code) from error
        except InvalidIntegrationMessage as error:
            self._settlement.dead_letter(message, error.code, error.field_paths)
            self._record(
                ProcessingOutcome.DEAD_LETTERED,
                message.message_id,
                message.correlation_id,
                error.code,
                started_at,
            )
            return ProcessingOutcome.DEAD_LETTERED
        except PermanentDeliveryError as error:
            self._settlement.dead_letter(message, error.code, ())
            self._record(
                ProcessingOutcome.DEAD_LETTERED,
                message.message_id,
                message.correlation_id,
                error.code,
                started_at,
            )
            return ProcessingOutcome.DEAD_LETTERED
        except Exception as error:
            self._settlement.abandon(message)
            reason = error.code if isinstance(error, TransientDeliveryError) else "processing_failure"
            self._record(
                ProcessingOutcome.ABANDONED,
                message.message_id,
                message.correlation_id,
                reason,
                started_at,
            )
            return ProcessingOutcome.ABANDONED

        self._settlement.complete(message)
        outcome = (
            ProcessingOutcome.DUPLICATE
            if delivery_status == "duplicate"
            else ProcessingOutcome.COMPLETED
        )
        self._record(
            outcome,
            message.message_id,
            message.correlation_id,
            None,
            started_at,
        )
        return outcome

    def run(self, stop_requested: Callable[[], bool]) -> None:
        while not stop_requested():
            self.process_next()

