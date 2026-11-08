import re
from typing import Any

from azure.identity import DefaultAzureCredential
from azure.servicebus import AutoLockRenewer, ServiceBusClient, ServiceBusReceiveMode

from ..application.integration_ports import ReceivedMessage
from .settings import RuntimeSettings


_TRACEPARENT = re.compile(
    r"^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$"
)


class ServiceBusMessageError(ValueError):
    """Raised when broker metadata cannot satisfy the application contract."""


def _application_property(message: Any, name: str) -> str | None:
    properties = message.application_properties or {}
    value = properties.get(name)
    if value is None:
        value = properties.get(name.encode())
    if isinstance(value, bytes):
        try:
            value = value.decode()
        except UnicodeDecodeError:
            return None
    return value if isinstance(value, str) else None


def _safe_trace_context(message: Any) -> tuple[str | None, str | None]:
    traceparent = _application_property(message, "traceparent")
    if not traceparent or not _TRACEPARENT.fullmatch(traceparent):
        return None, None
    parts = traceparent.split("-")
    if set(parts[1]) == {"0"} or set(parts[2]) == {"0"}:
        return None, None
    tracestate = _application_property(message, "tracestate")
    if tracestate is not None and len(tracestate) > 512:
        tracestate = None
    return traceparent, tracestate


def _body_bytes(message: Any) -> bytes:
    try:
        parts = list(message.body)
    except (TypeError, ValueError) as error:
        raise ServiceBusMessageError("unsupported_message_body") from error
    if not parts or any(not isinstance(part, bytes) for part in parts):
        raise ServiceBusMessageError("unsupported_message_body")
    return b"".join(parts)


class AzureServiceBusAdapter:
    def __init__(
        self,
        receiver: Any,
        client: Any | None = None,
        lock_renewer: Any | None = None,
        max_lock_renewal_seconds: float = 300,
    ) -> None:
        self._receiver = receiver
        self._client = client
        self._lock_renewer = lock_renewer
        self._max_lock_renewal_seconds = max_lock_renewal_seconds

    @classmethod
    def connect(
        cls,
        settings: RuntimeSettings,
        credential: Any | None = None,
    ) -> "AzureServiceBusAdapter":
        resolved_credential = credential or DefaultAzureCredential()
        client = ServiceBusClient(
            fully_qualified_namespace=settings.service_bus_namespace,
            credential=resolved_credential,
            logging_enable=False,
        )
        receiver = client.get_subscription_receiver(
            topic_name=settings.service_bus_topic,
            subscription_name=settings.service_bus_subscription,
            receive_mode=ServiceBusReceiveMode.PEEK_LOCK,
            max_wait_time=settings.service_bus_receive_wait_seconds,
        )
        return cls(
            receiver,
            client,
            AutoLockRenewer(),
            settings.service_bus_max_lock_renewal_seconds,
        )

    def receive(self) -> ReceivedMessage | None:
        messages = self._receiver.receive_messages(max_message_count=1)
        if not messages:
            return None
        raw = messages[0]
        if self._lock_renewer is not None:
            self._lock_renewer.register(
                self._receiver,
                raw,
                max_lock_renewal_duration=self._max_lock_renewal_seconds,
            )
        traceparent, tracestate = _safe_trace_context(raw)
        broker_event_type = _application_property(raw, "event_type")
        broker_schema_version = _application_property(raw, "schema_version")
        contract_error = None
        if raw.message_id is None or raw.correlation_id is None:
            contract_error = "missing_broker_identifier"
        try:
            body = _body_bytes(raw)
        except ServiceBusMessageError:
            body = b""
            contract_error = "unsupported_message_body"
        if (
            contract_error is None
            and (broker_event_type is None or broker_schema_version is None)
        ):
            contract_error = "missing_broker_application_property"
        return ReceivedMessage(
            body=body,
            message_id=str(raw.message_id) if raw.message_id is not None else "",
            correlation_id=str(raw.correlation_id) if raw.correlation_id is not None else "",
            traceparent=traceparent,
            tracestate=tracestate,
            broker_event_type=broker_event_type,
            broker_schema_version=broker_schema_version,
            contract_error=contract_error,
            settlement_token=raw,
        )

    @staticmethod
    def _raw(message: ReceivedMessage) -> Any:
        if message.settlement_token is None:
            raise ServiceBusMessageError("missing_settlement_token")
        return message.settlement_token

    def complete(self, message: ReceivedMessage) -> None:
        self._receiver.complete_message(self._raw(message))

    def abandon(self, message: ReceivedMessage) -> None:
        self._receiver.abandon_message(self._raw(message))

    def dead_letter(
        self, message: ReceivedMessage, reason: str, field_paths: tuple[str, ...]
    ) -> None:
        description = ",".join(field_paths) if field_paths else "no_field_paths"
        self._receiver.dead_letter_message(
            self._raw(message), reason=reason, error_description=description
        )

    def close(self) -> None:
        if self._lock_renewer is not None:
            self._lock_renewer.close()
        self._receiver.close()
        if self._client is not None:
            self._client.close()

    def __enter__(self) -> "AzureServiceBusAdapter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

