import json

import pytest

from pipesor_nlp_log_processor import (
    DrainLogParser,
    PatternLogSanitizer,
    ProcessEvidence,
    ProcessLog,
    ReceivedMessage,
)
from pipesor_nlp_log_processor.application.integration_ports import (
    AuthorizationDeliveryError,
    DeliveryStatus,
    PermanentDeliveryError,
    TransientDeliveryError,
)
from pipesor_nlp_log_processor.application.message_loop import (
    ConsumptionPaused,
    MessageLoop,
    ProcessingOutcome,
)
from pipesor_nlp_log_processor.infrastructure.simulated import (
    InMemoryMessageReceiver,
    RecordingEvidenceDelivery,
    RecordingObserver,
    RecordingSettlement,
)


def message(body: bytes | None = None) -> ReceivedMessage:
    payload = {
        "schema_version": "1.0",
        "event_type": "protected_workflow_evidence",
        "message_id": "msg-1",
        "occurred_at": "2026-11-07T07:30:00Z",
        "correlation_id": "corr-1",
        "source": "github_actions",
        "pipeline": {"repository": "pipesor-org/example", "workflow": "ci"},
        "execution": {"execution_id": "exec-1", "external_run_id": "42"},
        "evidence": {
            "stage": "BUILD",
            "summary": "Build failed",
            "excerpt": "status=500",
        },
    }
    return ReceivedMessage(body or json.dumps(payload).encode(), "msg-1", "corr-1")


class FailingDelivery:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def deliver(self, evidence: object) -> None:
        raise self.error


def loop_for(
    received: ReceivedMessage,
    delivery: object,
) -> tuple[MessageLoop, RecordingSettlement, RecordingObserver]:
    settlement = RecordingSettlement()
    observer = RecordingObserver()
    processor = ProcessEvidence(
        ProcessLog(DrainLogParser(), PatternLogSanitizer()), delivery  # type: ignore[arg-type]
    )
    return (
        MessageLoop(
            InMemoryMessageReceiver([received]),
            settlement,
            processor,
            observer,
            clock=lambda: 0.0,
        ),
        settlement,
        observer,
    )


def test_completes_only_after_successful_delivery() -> None:
    loop, settlement, observer = loop_for(message(), RecordingEvidenceDelivery())

    assert loop.process_next() is ProcessingOutcome.COMPLETED
    assert settlement.completed == [message()]
    assert not settlement.abandoned
    assert observer.records == [("completed", "msg-1", "corr-1", None, 0.0)]


def test_records_duplicate_as_distinct_successful_outcome() -> None:
    loop, settlement, observer = loop_for(
        message(), RecordingEvidenceDelivery(DeliveryStatus.DUPLICATE)
    )

    assert loop.process_next() is ProcessingOutcome.DUPLICATE
    assert settlement.completed == [message()]
    assert observer.records == [("duplicate", "msg-1", "corr-1", None, 0.0)]


def test_dead_letters_invalid_contract_with_safe_reason() -> None:
    loop, settlement, observer = loop_for(message(b"not-json"), RecordingEvidenceDelivery())

    assert loop.process_next() is ProcessingOutcome.DEAD_LETTERED
    assert settlement.dead_lettered[0][1:] == ("invalid_json", ())
    assert observer.records[0] == (
        "dead_lettered",
        "msg-1",
        "corr-1",
        "invalid_json",
        0.0,
    )


def test_abandons_transient_delivery_failure() -> None:
    loop, settlement, observer = loop_for(
        message(), FailingDelivery(TransientDeliveryError("correlation_unavailable"))
    )

    assert loop.process_next() is ProcessingOutcome.ABANDONED
    assert settlement.abandoned == [message()]
    assert observer.records[0][3] == "correlation_unavailable"


def test_dead_letters_permanent_delivery_rejection() -> None:
    loop, settlement, _ = loop_for(
        message(), FailingDelivery(PermanentDeliveryError("contract_rejected"))
    )

    assert loop.process_next() is ProcessingOutcome.DEAD_LETTERED
    assert settlement.dead_lettered[0][1] == "contract_rejected"


def test_abandons_current_message_and_pauses_on_authorization_failure() -> None:
    loop, settlement, observer = loop_for(
        message(), FailingDelivery(AuthorizationDeliveryError("correlation_unauthorized"))
    )

    with pytest.raises(ConsumptionPaused, match="correlation_unauthorized"):
        loop.process_next()

    assert settlement.abandoned == [message()]
    assert not settlement.dead_lettered
    assert observer.records[0] == (
        "paused",
        "msg-1",
        "corr-1",
        "correlation_unauthorized",
        0.0,
    )


def test_abandons_unexpected_failure_without_exposing_exception() -> None:
    loop, settlement, observer = loop_for(
        message(), FailingDelivery(RuntimeError("sensitive downstream response"))
    )

    assert loop.process_next() is ProcessingOutcome.ABANDONED
    assert settlement.abandoned == [message()]
    assert observer.records[0][3] == "processing_failure"
    assert "sensitive" not in repr(observer.records)


def test_returns_idle_when_no_message_is_available() -> None:
    settlement = RecordingSettlement()
    observer = RecordingObserver()
    processor = ProcessEvidence(
        ProcessLog(DrainLogParser(), PatternLogSanitizer()), RecordingEvidenceDelivery()
    )
    loop = MessageLoop(InMemoryMessageReceiver(), settlement, processor, observer)

    assert loop.process_next() is ProcessingOutcome.IDLE
    assert not observer.records


def test_run_stops_before_receiving_after_shutdown_request() -> None:
    settlement = RecordingSettlement()
    observer = RecordingObserver()
    processor = ProcessEvidence(
        ProcessLog(DrainLogParser(), PatternLogSanitizer()), RecordingEvidenceDelivery()
    )
    loop = MessageLoop(InMemoryMessageReceiver([message()]), settlement, processor, observer)

    loop.run(lambda: True)

    assert not settlement.completed
