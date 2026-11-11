from collections.abc import Iterable

import pytest

from pipesor_nlp_log_processor.application.integration_ports import ReceivedMessage
from pipesor_nlp_log_processor.infrastructure.service_bus import (
    AzureServiceBusAdapter,
    ServiceBusMessageError,
)


VALID_TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


class RawMessage:
    def __init__(
        self,
        body: Iterable[bytes] = (b'{"schema_version":"2.0"}',),
        message_id: object = "msg-1",
        correlation_id: object = "corr-1",
        properties: dict[object, object] | None = None,
    ) -> None:
        self.body = body
        self.message_id = message_id
        self.correlation_id = correlation_id
        self.application_properties = {
            "eventType": "protected_workflow_evidence",
            "schemaMajor": 2,
            **(properties or {}),
        }


class FakeReceiver:
    def __init__(self, messages: list[RawMessage]) -> None:
        self.messages = messages
        self.completed: list[RawMessage] = []
        self.abandoned: list[RawMessage] = []
        self.dead_lettered: list[tuple[RawMessage, str, str]] = []
        self.closed = False

    def receive_messages(self, max_message_count: int) -> list[RawMessage]:
        assert max_message_count == 1
        return [self.messages.pop(0)] if self.messages else []

    def complete_message(self, message: RawMessage) -> None:
        self.completed.append(message)

    def abandon_message(self, message: RawMessage) -> None:
        self.abandoned.append(message)

    def dead_letter_message(
        self, message: RawMessage, reason: str, error_description: str
    ) -> None:
        self.dead_lettered.append((message, reason, error_description))

    def close(self) -> None:
        self.closed = True


def test_receives_peek_lock_message_and_preserves_safe_trace_context() -> None:
    class FakeLockRenewer:
        registration: tuple[object, object, float] | None = None

        def register(
            self, receiver: object, message: object, max_lock_renewal_duration: float
        ) -> None:
            self.registration = (receiver, message, max_lock_renewal_duration)

    raw = RawMessage(
        body=(b"first", b"-second"),
        properties={b"traceparent": VALID_TRACEPARENT.encode(), "tracestate": "vendor=value"},
    )
    receiver = FakeReceiver([raw])
    renewer = FakeLockRenewer()
    adapter = AzureServiceBusAdapter(receiver, lock_renewer=renewer, max_lock_renewal_seconds=180)

    received = adapter.receive()

    assert received is not None
    assert received.body == b"first-second"
    assert received.message_id == "msg-1"
    assert received.correlation_id == "corr-1"
    assert received.traceparent == VALID_TRACEPARENT
    assert received.tracestate == "vendor=value"
    assert received.broker_event_type == "protected_workflow_evidence"
    assert received.broker_schema_major == 2
    assert received.settlement_token is raw
    assert renewer.registration == (receiver, raw, 180)


def test_drops_invalid_trace_context_instead_of_forwarding_it() -> None:
    raw = RawMessage(properties={"traceparent": "invalid", "tracestate": "secret"})
    received = AzureServiceBusAdapter(FakeReceiver([raw])).receive()
    assert received is not None
    assert received.traceparent is None
    assert received.tracestate is None


def test_marks_missing_filter_properties_for_dead_letter() -> None:
    raw = RawMessage()
    raw.application_properties = {}

    received = AzureServiceBusAdapter(FakeReceiver([raw])).receive()

    assert received is not None
    assert received.contract_error == "missing_broker_application_property"


@pytest.mark.parametrize("schema_major", ["2", True, 2.0])
def test_rejects_non_integer_schema_major(schema_major: object) -> None:
    raw = RawMessage(properties={"schemaMajor": schema_major})
    received = AzureServiceBusAdapter(FakeReceiver([raw])).receive()

    assert received is not None
    assert received.contract_error == "missing_broker_application_property"


def test_settles_the_original_sdk_message() -> None:
    raw = RawMessage()
    receiver = FakeReceiver([raw])
    adapter = AzureServiceBusAdapter(receiver)
    received = adapter.receive()
    assert received is not None

    adapter.complete(received)
    adapter.abandon(received)
    adapter.dead_letter(received, "invalid_json", ("evidence.excerpt",))

    assert receiver.completed == [raw]
    assert receiver.abandoned == [raw]
    assert receiver.dead_lettered == [(raw, "invalid_json", "evidence.excerpt")]


def test_marks_invalid_broker_messages_for_safe_dead_letter_settlement() -> None:
    missing_id = AzureServiceBusAdapter(
        FakeReceiver([RawMessage(message_id=None)])
    ).receive()
    unsupported_body = AzureServiceBusAdapter(
        FakeReceiver([RawMessage(body=("secret",))])
    ).receive()

    assert missing_id is not None
    assert missing_id.contract_error == "missing_broker_identifier"
    assert unsupported_body is not None
    assert unsupported_body.contract_error == "unsupported_message_body"
    assert unsupported_body.body == b""
    assert "secret" not in repr(unsupported_body)


def test_requires_settlement_token() -> None:
    adapter = AzureServiceBusAdapter(FakeReceiver([]))
    with pytest.raises(ServiceBusMessageError, match="missing_settlement_token"):
        adapter.complete(ReceivedMessage(b"{}", "msg-1", "corr-1"))


def test_closes_receiver_and_client() -> None:
    class FakeClient:
        closed = False

        def close(self) -> None:
            self.closed = True

    receiver = FakeReceiver([])
    client = FakeClient()
    class FakeLockRenewer:
        closed = False

        def close(self) -> None:
            self.closed = True

    renewer = FakeLockRenewer()
    adapter = AzureServiceBusAdapter(receiver, client, renewer)

    adapter.close()

    assert receiver.closed
    assert client.closed
    assert renewer.closed
