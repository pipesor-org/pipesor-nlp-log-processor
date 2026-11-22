import json
import io
import urllib.error
from email.message import Message
from collections import deque
from collections.abc import Iterable
from typing import Mapping

import pytest

from pipesor_nlp_log_processor.application.integration_ports import (
    AuthorizationDeliveryError,
    DeliveryStatus,
    PermanentDeliveryError,
    TransientDeliveryError,
)
from pipesor_nlp_log_processor.domain.integration import (
    CaptureContext,
    CommitContext,
    CorrelationEvidence,
    ExecutionContext,
    PipelineContext,
)
from pipesor_nlp_log_processor.infrastructure.correlation_http import (
    CorrelationHttpDelivery,
    HttpResponse,
    RetryPolicy,
    TransportFailure,
    UrlLibHttpTransport,
)


class StaticTokenProvider:
    def get_token(self) -> str:
        return "test-token"


class RecordingRetryObserver:
    def __init__(self) -> None:
        self.records: list[tuple[str, int, float]] = []

    def record_retry(self, reason: str, attempt: int, delay_seconds: float) -> None:
        self.records.append((reason, attempt, delay_seconds))


class StubTransport:
    def __init__(self, results: Iterable[HttpResponse | Exception]) -> None:
        self.results = deque(results)
        self.requests: list[tuple[str, bytes, Mapping[str, str], float]] = []

    def post(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HttpResponse:
        self.requests.append((url, body, headers, timeout_seconds))
        result = self.results.popleft()
        if isinstance(result, Exception):
            raise result
        return result


def evidence() -> CorrelationEvidence:
    return CorrelationEvidence(
        contract_version="2.0",
        message_id="msg-1",
        correlation_id="corr-1",
        source="github_actions",
        occurred_at="2026-11-07T07:30:00Z",
        source_updated_at="2026-11-07T07:31:00Z",
        capture_context=CaptureContext("tenant-1", "1", "2", "scope-1"),
        pipeline=PipelineContext("3", "4"),
        execution=ExecutionContext("5", 1, "completed", "failure"),
        commit=CommitContext("a" * 40),
        service=None,
        environment=None,
        pipeline_id="pipeline-1",
        execution_id="exec-1",
        incident_id="incident-1",
        evidence_id="evidence-1",
        stage="BUILD",
        job=None,
        sequence=3,
        sanitized_summary="safe summary",
        sanitized_content="safe content",
        template="safe content",
    )


def delivery(
    transport: StubTransport,
    attempts: int = 3,
    sleeps: list[float] | None = None,
) -> CorrelationHttpDelivery:
    return CorrelationHttpDelivery(
        "https://correlation.internal",
        2.5,
        RetryPolicy(attempts, 0.5, 40),
        StaticTokenProvider(),
        transport,
        (sleeps if sleeps is not None else []).append,
    )


@pytest.mark.parametrize("status", ["accepted", "duplicate"])
def test_delivers_contract_with_authentication_and_idempotency(status: str) -> None:
    transport = StubTransport(
        [HttpResponse(200, json.dumps({"status": status, "message_id": "msg-1"}).encode())]
    )

    result = delivery(transport).deliver(evidence())

    assert result is DeliveryStatus(status)
    url, body, headers, timeout = transport.requests[0]
    assert url == "https://correlation.internal/internal/v1/processed-evidence"
    payload = json.loads(body)
    assert payload["contract_version"] == "2.0"
    assert payload["sanitized_content"] == "safe content"
    assert payload["capture_context"]["tenant_id"] == "tenant-1"
    assert payload["execution"]["run_attempt"] == 1
    assert payload["incident_id"] == "incident-1"
    assert "job" not in payload
    assert headers["Authorization"] == "Bearer test-token"
    assert headers["Idempotency-Key"] == "msg-1"
    assert headers["X-Correlation-ID"] == "corr-1"
    assert timeout == 2.5


def test_retries_transient_responses_with_bounded_backoff() -> None:
    sleeps: list[float] = []
    transport = StubTransport(
        [
            HttpResponse(503, b"sensitive response"),
            HttpResponse(429, b"sensitive response"),
            HttpResponse(200, b'{"status":"accepted","message_id":"msg-1"}'),
        ]
    )

    assert delivery(transport, sleeps=sleeps).deliver(evidence()) is DeliveryStatus.ACCEPTED
    assert len(transport.requests) == 3
    assert sleeps == [0.5, 1.0]


def test_records_retry_metrics_without_message_identifiers() -> None:
    observer = RecordingRetryObserver()
    transport = StubTransport(
        [
            HttpResponse(503, b""),
            HttpResponse(200, b'{"status":"accepted","message_id":"msg-1"}'),
        ]
    )
    adapter = CorrelationHttpDelivery(
        "https://correlation.internal",
        2.5,
        RetryPolicy(3, 0.5, 40),
        StaticTokenProvider(),
        transport,
        lambda _: None,
        observer=observer,
    )

    assert adapter.deliver(evidence()) is DeliveryStatus.ACCEPTED
    assert observer.records == [("correlation_unavailable", 1, 0.5)]


def test_exhausted_network_failures_are_safe_and_transient() -> None:
    transport = StubTransport(
        [TransportFailure("secret host detail"), TransportFailure("secret host detail")]
    )

    with pytest.raises(TransientDeliveryError, match="correlation_unavailable") as caught:
        delivery(transport, attempts=2).deliver(evidence())

    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (400, b"sensitive rejection"),
        (409, b"sensitive conflict"),
        (413, b'{"status":413,"code":"REQUEST_TOO_LARGE","detail":"sensitive rejection"}'),
        (422, b"sensitive malformed response"),
        (422, b'{"code":"sensitive_unknown_reason"}'),
        (422, b'{"status":413,"code":"RESOURCE_DENIED"}'),
        (413, b'{"status":413,"code":"RESOURCE_DENIED"}'),
        (422, b'{"status":422.0,"code":"RESOURCE_DENIED"}'),
        (422, b'{"status":true,"code":"RESOURCE_DENIED"}'),
        (422, b'{"status":422,"code":"RESOURCE_DENIED","code":"RESOURCE_DENIED"}'),
        (422, b'{"status":422,"code":["RESOURCE_DENIED"]}'),
        (422, b'{"status":422,"code":"RESOURCE_DENIED","detail":NaN}'),
        (422, b'{"status":422,"code":"RESOURCE_DENIED"}' + b" " * 4096),
        (422, b'{"status":422,"code":"RESOURCE_DENIED","detail":"\xff"}'),
    ],
)
def test_does_not_retry_permanent_contract_rejection(status: int, body: bytes) -> None:
    transport = StubTransport(
        [HttpResponse(status, body, {"Content-Type": "application/problem+json"})]
    )
    sleeps: list[float] = []

    with pytest.raises(PermanentDeliveryError, match="correlation_contract_rejected") as caught:
        delivery(transport, sleeps=sleeps).deliver(evidence())

    assert caught.value.code == "correlation_contract_rejected"
    assert str(caught.value) == "correlation_contract_rejected"
    assert caught.value.__cause__ is None
    assert len(transport.requests) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    ("status", "remote_code", "reason"),
    [(422, "RESOURCE_DENIED", "correlation_resource_denied"),
     (413, "CAPACITY_EXCEEDED", "correlation_capacity_exceeded")],
)
def test_preserves_only_approved_permanent_reasons(status, remote_code, reason) -> None:
    transport = StubTransport([HttpResponse(status, json.dumps({
        "status": status, "code": remote_code, "detail": "sensitive value"
    }).encode(), {"content-type": "Application/Problem+JSON; charset=utf-8"})])
    sleeps: list[float] = []
    with pytest.raises(PermanentDeliveryError) as caught:
        delivery(transport, sleeps=sleeps).deliver(evidence())
    assert caught.value.code == reason
    assert str(caught.value) == reason
    assert caught.value.__cause__ is None
    assert len(transport.requests) == 1
    assert sleeps == []


@pytest.mark.parametrize("headers", [{}, {"Content-Type": "application/json"},
    {"Content-Type": "application/problem+json", "content-type": "application/problem+json"}])
def test_requires_unambiguous_problem_media_type(headers) -> None:
    transport = StubTransport([HttpResponse(422, b'{"status":422,"code":"RESOURCE_DENIED"}', headers)])
    with pytest.raises(PermanentDeliveryError, match="^correlation_contract_rejected$"):
        delivery(transport).deliver(evidence())


@pytest.mark.parametrize("unreadable", [False, True])
def test_bounds_problem_read_and_preserves_status_if_body_fails(monkeypatch, unreadable) -> None:
    class ResponseBody(io.BytesIO):
        reads = []

        def read(self, size=-1):
            self.reads.append(size)
            if unreadable:
                raise TimeoutError("sensitive value")
            return super().read(size)

    body = ResponseBody(b"x" * 10_000)
    error = urllib.error.HTTPError("https://correlation.internal", 422, "rejected",
                                  {"Content-Type": "application/problem+json"}, body)

    def reject(*args, **kwargs):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", reject)
    response = UrlLibHttpTransport().post("https://correlation.internal", b"{}", {}, 1)
    assert response.status_code == 422
    assert body.reads == [4097]
    assert len(response.body) == (0 if unreadable else 4097)
    assert body.closed
    with pytest.raises(PermanentDeliveryError, match="^correlation_contract_rejected$"):
        CorrelationHttpDelivery._interpret(response, "msg-1")


@pytest.mark.parametrize("fault", ["duplicate_type", "truncated", "duplicate_length"])
def test_real_transport_discards_ambiguous_headers_or_truncated_problem(monkeypatch, fault) -> None:
    raw = b'{"status":422,"code":"RESOURCE_DENIED"}'
    headers = Message()
    headers["Content-Type"] = "application/problem+json"
    headers["Content-Length"] = str(len(raw) + (1 if fault == "truncated" else 0))
    if fault == "duplicate_type":
        headers["Content-Type"] = "application/problem+json"
    if fault == "duplicate_length":
        headers["Content-Length"] = str(len(raw))
    error = urllib.error.HTTPError("https://correlation.internal", 422, "rejected", headers, io.BytesIO(raw))

    def reject(*args, **kwargs):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", reject)
    response = UrlLibHttpTransport().post("https://correlation.internal", b"{}", {}, 1)
    with pytest.raises(PermanentDeliveryError, match="^correlation_contract_rejected$"):
        CorrelationHttpDelivery._interpret(response, "msg-1")


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "correlation_unauthenticated"), (403, "correlation_unauthorized")],
)
def test_surfaces_authorization_failures_without_retry(status: int, code: str) -> None:
    transport = StubTransport([HttpResponse(status, b"sensitive rejection")])

    with pytest.raises(AuthorizationDeliveryError, match=code):
        delivery(transport).deliver(evidence())

    assert len(transport.requests) == 1


def test_retries_malformed_success_without_exposing_response() -> None:
    transport = StubTransport([HttpResponse(200, b"sensitive malformed response")])

    with pytest.raises(TransientDeliveryError, match="invalid_correlation_response") as caught:
        delivery(transport, attempts=1).deliver(evidence())

    assert "sensitive" not in str(caught.value)


def test_honors_retry_after_within_total_deadline() -> None:
    sleeps: list[float] = []
    transport = StubTransport(
        [
            HttpResponse(429, b"", {"Retry-After": "2"}),
            HttpResponse(200, b'{"status":"accepted","message_id":"msg-1"}'),
        ]
    )

    assert delivery(transport, sleeps=sleeps).deliver(evidence()) is DeliveryStatus.ACCEPTED
    assert sleeps == [2]


def test_stops_when_retry_after_exceeds_total_deadline() -> None:
    transport = StubTransport([HttpResponse(429, b"", {"Retry-After": "50"})])
    adapter = CorrelationHttpDelivery(
        "https://correlation.internal",
        10,
        RetryPolicy(3, 1, 20),
        StaticTokenProvider(),
        transport,
        lambda _: None,
    )

    with pytest.raises(
        TransientDeliveryError, match="correlation_delivery_deadline_exceeded"
    ):
        adapter.deliver(evidence())

    assert len(transport.requests) == 1
