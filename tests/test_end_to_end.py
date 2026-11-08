import json
from typing import Mapping

from pipesor_nlp_log_processor import DrainLogParser, PatternLogSanitizer, ProcessEvidence, ProcessLog
from pipesor_nlp_log_processor.application.message_loop import MessageLoop, ProcessingOutcome
from pipesor_nlp_log_processor.infrastructure.correlation_http import (
    CorrelationHttpDelivery,
    HttpResponse,
    RetryPolicy,
)
from pipesor_nlp_log_processor.infrastructure.service_bus import AzureServiceBusAdapter
from pipesor_nlp_log_processor.infrastructure.simulated import RecordingObserver


TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


class RawMessage:
    message_id = "msg-1"
    correlation_id = "corr-1"
    application_properties = {
        "event_type": "protected_workflow_evidence",
        "schema_version": "1.0",
        "traceparent": TRACEPARENT,
    }

    def __init__(self) -> None:
        self.body = [
            json.dumps(
                {
                    "schema_version": "1.0",
                    "event_type": "protected_workflow_evidence",
                    "message_id": "msg-1",
                    "occurred_at": "2026-11-08T00:00:00Z",
                    "correlation_id": "corr-1",
                    "source": "github_actions",
                    "pipeline": {
                        "repository": "pipesor-org/example",
                        "workflow": "ci",
                    },
                    "execution": {
                        "execution_id": "exec-1",
                        "external_run_id": "42",
                    },
                    "evidence": {
                        "stage": "BUILD",
                        "summary": "Build failed",
                        "excerpt": "request failed token=secret-value status=500",
                    },
                }
            ).encode()
        ]


class Receiver:
    def __init__(self, raw: RawMessage) -> None:
        self.raw = raw
        self.completed: list[RawMessage] = []
        self.abandoned: list[RawMessage] = []

    def receive_messages(self, max_message_count: int) -> list[RawMessage]:
        return [self.raw]

    def complete_message(self, message: RawMessage) -> None:
        self.completed.append(message)

    def abandon_message(self, message: RawMessage) -> None:
        self.abandoned.append(message)


class TokenProvider:
    def get_token(self) -> str:
        return "test-token"


class CorrelationTransport:
    def __init__(self) -> None:
        self.body = b""
        self.headers: Mapping[str, str] = {}

    def post(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HttpResponse:
        self.body = body
        self.headers = headers
        return HttpResponse(200, b'{"status":"duplicate","message_id":"msg-1"}')


def test_service_bus_to_sanitized_correlation_duplicate_flow() -> None:
    raw = RawMessage()
    receiver = Receiver(raw)
    service_bus = AzureServiceBusAdapter(receiver)
    transport = CorrelationTransport()
    delivery = CorrelationHttpDelivery(
        "https://correlation.internal",
        10,
        RetryPolicy(3, 1, 40),
        TokenProvider(),
        transport,
        lambda _: None,
    )
    observer = RecordingObserver()
    loop = MessageLoop(
        service_bus,
        service_bus,
        ProcessEvidence(ProcessLog(DrainLogParser(), PatternLogSanitizer()), delivery),
        observer,
        clock=lambda: 0.0,
    )

    assert loop.process_next() is ProcessingOutcome.DUPLICATE
    assert receiver.completed == [raw]
    assert not receiver.abandoned
    delivered = json.loads(transport.body)
    assert "secret-value" not in delivered["sanitized_content"]
    assert delivered["message_id"] == "msg-1"
    assert transport.headers["traceparent"] == TRACEPARENT
    assert observer.records == [("duplicate", "msg-1", "corr-1", None, 0.0)]
