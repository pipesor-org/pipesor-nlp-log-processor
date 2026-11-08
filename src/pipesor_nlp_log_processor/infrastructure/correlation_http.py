import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol

from ..application.integration_ports import (
    AuthorizationDeliveryError,
    DeliveryStatus,
    PermanentDeliveryError,
    TransientDeliveryError,
)
from ..domain.integration import CorrelationEvidence


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


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


class AzureCredentialTokenProvider:
    def __init__(self, credential: object, scope: str) -> None:
        self._credential = credential
        self._scope = scope

    def get_token(self) -> str:
        try:
            token = self._credential.get_token(self._scope)  # type: ignore[attr-defined]
            value = token.token
            if not isinstance(value, str) or not value:
                raise ValueError
            return value
        except Exception as error:
            raise TransientDeliveryError("correlation_token_unavailable") from error


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
                return HttpResponse(
                    response.status, response.read(), dict(response.headers.items())
                )
        except urllib.error.HTTPError as error:
            headers = dict(error.headers.items()) if error.headers else {}
            return HttpResponse(error.code, error.read(), headers)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise TransportFailure("correlation_transport_failure") from error


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    backoff_seconds: float
    total_timeout_seconds: float

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        if not 0 <= self.backoff_seconds <= 30:
            raise ValueError("backoff_seconds must be between 0 and 30")
        if not 0 < self.total_timeout_seconds <= 300:
            raise ValueError("total_timeout_seconds must be between 0 and 300")


class CorrelationHttpDelivery:
    def __init__(
        self,
        base_endpoint: str,
        timeout_seconds: float,
        retry_policy: RetryPolicy,
        token_provider: TokenProvider,
        transport: HttpTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not base_endpoint.startswith("https://"):
            raise ValueError("base_endpoint must use HTTPS")
        self._url = f"{base_endpoint.rstrip('/')}/internal/v1/processed-evidence"
        self._timeout_seconds = timeout_seconds
        self._retry_policy = retry_policy
        self._token_provider = token_provider
        self._transport = transport or UrlLibHttpTransport()
        self._sleep = sleep
        self._clock = clock

    def deliver(self, evidence: CorrelationEvidence) -> DeliveryStatus:
        body = self._serialize(evidence)
        last_error: TransientDeliveryError | None = None
        deadline = self._clock() + self._retry_policy.total_timeout_seconds
        for attempt in range(1, self._retry_policy.max_attempts + 1):
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise TransientDeliveryError("correlation_delivery_deadline_exceeded")
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
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise TransientDeliveryError(
                        "correlation_delivery_deadline_exceeded"
                    )
                response = self._transport.post(
                    self._url,
                    body,
                    headers,
                    min(self._timeout_seconds, remaining),
                )
                return self._interpret(response, evidence.message_id)
            except TransportFailure as error:
                last_error = TransientDeliveryError("correlation_unavailable")
                last_error.__cause__ = error
            except TransientDeliveryError as error:
                last_error = error

            if attempt < self._retry_policy.max_attempts:
                remaining = deadline - self._clock()
                delay = (
                    last_error.retry_after_seconds
                    if last_error is not None
                    and last_error.retry_after_seconds is not None
                    else self._retry_policy.backoff_seconds * attempt
                )
                if remaining <= 0 or delay >= remaining:
                    raise TransientDeliveryError(
                        "correlation_delivery_deadline_exceeded"
                    )
                self._sleep(delay)

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
            retry_after = None
            header = next(
                (
                    value
                    for name, value in response.headers.items()
                    if name.lower() == "retry-after"
                ),
                None,
            )
            if header is not None:
                try:
                    parsed = float(header)
                    retry_after = parsed if parsed >= 0 else None
                except ValueError:
                    retry_after = None
            raise TransientDeliveryError("correlation_unavailable", retry_after)
        if response.status_code == 401:
            raise AuthorizationDeliveryError("correlation_unauthenticated")
        if response.status_code == 403:
            raise AuthorizationDeliveryError("correlation_unauthorized")
        if 400 <= response.status_code < 500:
            raise PermanentDeliveryError("correlation_contract_rejected")
        raise TransientDeliveryError("invalid_correlation_response")

