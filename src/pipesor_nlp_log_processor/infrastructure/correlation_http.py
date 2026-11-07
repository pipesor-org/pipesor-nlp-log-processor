import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from ..application.integration_ports import (
    DeliveryStatus,
    PermanentDeliveryError,
    TransientDeliveryError,
)
from ..domain.integration import CorrelationEvidence


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    body: bytes


class HttpTransport(Protocol):
    def post(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HttpResponse: ...


class TokenProvider(Protocol):
    def get_token(self) -> str: ...


class TransportFailure(RuntimeError):
    """A safe network failure without response or payload details."""


class UrlLibHttpTransport:
    def post(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HttpResponse:
        request = urllib.request.Request(url, body, headers=dict(headers), method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return HttpResponse(response.status, response.read())
        except urllib.error.HTTPError as error:
            return HttpResponse(error.code, error.read())
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise TransportFailure("correlation_transport_failure") from error


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    backoff_seconds: float

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        if not 0 <= self.backoff_seconds <= 30:
            raise ValueError("backoff_seconds must be between 0 and 30")


class CorrelationHttpDelivery:
    def __init__(
        self,
        base_endpoint: str,
        timeout_seconds: float,
        retry_policy: RetryPolicy,
        token_provider: TokenProvider,
        transport: HttpTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not base_endpoint.startswith("https://"):
            raise ValueError("base_endpoint must use HTTPS")
        self._url = f"{base_endpoint.rstrip('/')}/internal/v1/processed-evidence"
        self._timeout_seconds = timeout_seconds
        self._retry_policy = retry_policy
        self._token_provider = token_provider
        self._transport = transport or UrlLibHttpTransport()
        self._sleep = sleep

    def deliver(self, evidence: CorrelationEvidence) -> DeliveryStatus:
        body = self._serialize(evidence)
        last_error: TransientDeliveryError | None = None
        for attempt in range(1, self._retry_policy.max_attempts + 1):
            try:
                headers = {
                    "Authorization": f"Bearer {self._token_provider.get_token()}",
                    "Content-Type": "application/json; charset=utf-8",
                    "Accept": "application/json",
                    "X-Correlation-ID": evidence.correlation_id,
                    "Idempotency-Key": evidence.message_id,
                }
                if evidence.traceparent:
                    headers["traceparent"] = evidence.traceparent
                if evidence.traceparent and evidence.tracestate:
                    headers["tracestate"] = evidence.tracestate
                response = self._transport.post(
                    self._url,
                    body,
                    headers,
                    self._timeout_seconds,
                )
                return self._interpret(response, evidence.message_id)
            except TransportFailure as error:
                last_error = TransientDeliveryError("correlation_unavailable")
                last_error.__cause__ = error
            except TransientDeliveryError as error:
                last_error = error

            if attempt < self._retry_policy.max_attempts:
                self._sleep(self._retry_policy.backoff_seconds * attempt)

        raise last_error or TransientDeliveryError("correlation_unavailable")

    @staticmethod
    def _serialize(evidence: CorrelationEvidence) -> bytes:
        payload = {
            "contract_version": evidence.contract_version,
            "message_id": evidence.message_id,
            "correlation_id": evidence.correlation_id,
            "execution_id": evidence.execution_id,
            "source": evidence.source,
            "observed_at": evidence.observed_at,
            "stage": evidence.stage,
            "sanitized_content": evidence.sanitized_content,
            "template": evidence.template,
        }
        if evidence.job is not None:
            payload["job"] = evidence.job
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()

    @staticmethod
    def _interpret(response: HttpResponse, expected_message_id: str) -> DeliveryStatus:
        if response.status_code == 200:
            try:
                payload = json.loads(response.body)
                status = DeliveryStatus(payload["status"])
                if payload["message_id"] != expected_message_id:
                    raise ValueError
                return status
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise TransientDeliveryError("invalid_correlation_response") from error
        if response.status_code in (429,) or response.status_code >= 500:
            raise TransientDeliveryError("correlation_unavailable")
        if response.status_code == 401:
            raise PermanentDeliveryError("correlation_unauthenticated")
        if response.status_code == 403:
            raise PermanentDeliveryError("correlation_unauthorized")
        if 400 <= response.status_code < 500:
            raise PermanentDeliveryError("correlation_contract_rejected")
        raise TransientDeliveryError("invalid_correlation_response")

