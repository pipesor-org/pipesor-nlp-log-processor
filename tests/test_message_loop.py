import json

import pytest

from pipesor_nlp_log_processor import (
    DrainLogParser,
    PatternLogSanitizer,
    ProcessEvidence,
    ProcessLog,
    ReceivedMessage,
)

MESSAGE_ID = "cf6b69cf-a28d-49f3-bceb-000000000001"
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
from pipesor_nlp_log_processor.infrastructure.correlation_http import (
    CorrelationHttpDelivery,
    HttpResponse,
    RetryPolicy,
)


def message(body: bytes | None = None) -> ReceivedMessage:
    payload = {
        "schema_version": "2.0",
        "event_type": "protected_workflow_evidence",
        "message_id": MESSAGE_ID,
        "occurred_at": "2026-11-07T07:30:00Z",
        "source_updated_at": "2026-11-07T07:31:00Z",
        "correlation_id": "corr-1",
        "source": "github_actions",
        "capture_context": {
            "tenant_id": "d91d1df6-ae89-40c2-99b5-20edfb3e9ed0",
            "github_account_id": "1",
            "github_installation_id": "2",
            "scope_revision": "scope-1",
        },
        "pipeline": {"github_repository_id": "3", "github_workflow_id": "4"},
        "execution": {
            "github_run_id": "42",
            "run_attempt": 1,
            "status": "completed",
            "conclusion": "failure",
        },
        "commit": {"sha": "a" * 40},
        "service": None,
        "environment": None,
        "evidence": {
            "stage": "BUILD",
            "summary": "Build failed",
            "excerpt": "status=500",
        },
    }
    return ReceivedMessage(
        body or json.dumps(payload).encode(),
        MESSAGE_ID,
        "corr-1",
        broker_event_type="protected_workflow_evidence",
        broker_schema_major=2,
    )


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
    assert observer.records == [("completed", MESSAGE_ID, "corr-1", None, 0.0)]


def test_records_duplicate_as_distinct_successful_outcome() -> None:
    loop, settlement, observer = loop_for(
        message(), RecordingEvidenceDelivery(DeliveryStatus.DUPLICATE)
    )

    assert loop.process_next() is ProcessingOutcome.DUPLICATE
    assert settlement.completed == [message()]
    assert observer.records == [("duplicate", MESSAGE_ID, "corr-1", None, 0.0)]


def test_dead_letters_invalid_contract_with_safe_reason() -> None:
    loop, settlement, observer = loop_for(message(b"not-json"), RecordingEvidenceDelivery())

    assert loop.process_next() is ProcessingOutcome.DEAD_LETTERED
    assert settlement.dead_lettered[0][1:] == ("invalid_json", ())
    assert observer.records[0] == (
        "dead_lettered",
        MESSAGE_ID,
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
        message(), FailingDelivery(PermanentDeliveryError("correlation_contract_rejected"))
    )

    assert loop.process_next() is ProcessingOutcome.DEAD_LETTERED
    assert settlement.dead_lettered[0][1:] == ("correlation_contract_rejected", ())
    assert not settlement.completed
    assert not settlement.abandoned


@pytest.mark.parametrize(
    ("status", "code", "reason"),
    [(422, "RESOURCE_DENIED", "correlation_resource_denied"),
     (413, "CAPACITY_EXCEEDED", "correlation_capacity_exceeded"),
     (413, "REQUEST_TOO_LARGE", "correlation_contract_rejected")],
)
def test_actual_http_rejections_reach_safe_dlq_reason_and_telemetry(status, code, reason) -> None:
    class Peer:
        def get_token(self):
            return "synthetic-token"

        def post(self, *args):
            return HttpResponse(status, json.dumps({
                "status": status, "code": code, "detail": "sensitive value"
            }).encode(), {"Content-Type": "application/problem+json"})

    peer = Peer()
    adapter = CorrelationHttpDelivery("https://correlation.internal", 1,
                                      RetryPolicy(1, 0, 1), peer, peer)
    loop, settlement, observer = loop_for(message(), adapter)
    assert loop.process_next() is ProcessingOutcome.DEAD_LETTERED
    assert settlement.dead_lettered[0][1:] == (reason, ())
    assert observer.records[0][3] == reason
    assert not settlement.completed
    assert not settlement.abandoned


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
        MESSAGE_ID,
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
