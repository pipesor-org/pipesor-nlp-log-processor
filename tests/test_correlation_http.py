import json
from collections import deque
from collections.abc import Iterable
from typing import Mapping

import pytest

from pipesor_nlp_log_processor.application.integration_ports import (
    DeliveryStatus,
    PermanentDeliveryError,
    TransientDeliveryError,
)
from pipesor_nlp_log_processor.domain.integration import CorrelationEvidence
from pipesor_nlp_log_processor.infrastructure.correlation_http import (
    CorrelationHttpDelivery,
    HttpResponse,
    RetryPolicy,
    TransportFailure,
)


class StaticTokenProvider:
    def get_token(self) -> str:
        return "test-token"


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
        contract_version="1.0",
        message_id="msg-1",
        correlation_id="corr-1",
        execution_id="exec-1",
        source="github_actions",
        observed_at="2026-11-07T07:30:00Z",
        stage="BUILD",
        job=None,
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
        RetryPolicy(attempts, 0.5),
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
    assert json.loads(body)["sanitized_content"] == "safe content"
    assert "job" not in json.loads(body)
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


def test_exhausted_network_failures_are_safe_and_transient() -> None:
    transport = StubTransport(
        [TransportFailure("secret host detail"), TransportFailure("secret host detail")]
    )

    with pytest.raises(TransientDeliveryError, match="correlation_unavailable") as caught:
        delivery(transport, attempts=2).deliver(evidence())

    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "correlation_contract_rejected"),
        (401, "correlation_unauthenticated"),
        (403, "correlation_unauthorized"),
    ],
)
def test_does_not_retry_permanent_rejections(status: int, code: str) -> None:
    transport = StubTransport([HttpResponse(status, b"sensitive rejection")])

    with pytest.raises(PermanentDeliveryError, match=code):
        delivery(transport).deliver(evidence())

    assert len(transport.requests) == 1


def test_retries_malformed_success_without_exposing_response() -> None:
    transport = StubTransport([HttpResponse(200, b"sensitive malformed response")])

    with pytest.raises(TransientDeliveryError, match="invalid_correlation_response") as caught:
        delivery(transport, attempts=1).deliver(evidence())

    assert "sensitive" not in str(caught.value)
