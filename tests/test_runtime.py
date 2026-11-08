import json
import logging
import signal
from threading import Event

import pytest

from pipesor_nlp_log_processor.application.integration_ports import TransientDeliveryError
from pipesor_nlp_log_processor.infrastructure.correlation_http import AzureCredentialTokenProvider
from pipesor_nlp_log_processor.infrastructure.observability import (
    JsonDeliveryObserver,
    JsonLoggingObserver,
)
from pipesor_nlp_log_processor.main import install_shutdown_handlers, main


class AccessToken:
    token = "safe-token"


class Credential:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.scopes: list[str] = []

    def get_token(self, scope: str) -> AccessToken:
        self.scopes.append(scope)
        if self.error:
            raise self.error
        return AccessToken()


def test_token_provider_requests_configured_entra_scope() -> None:
    credential = Credential()
    provider = AzureCredentialTokenProvider(
        credential, "api://correlation/.default"
    )

    assert provider.get_token() == "safe-token"
    assert credential.scopes == ["api://correlation/.default"]


def test_token_failure_is_transient_and_does_not_expose_details() -> None:
    provider = AzureCredentialTokenProvider(
        Credential(RuntimeError("sensitive credential detail")),
        "api://correlation/.default",
    )

    with pytest.raises(TransientDeliveryError, match="correlation_token_unavailable") as caught:
        provider.get_token()

    assert "sensitive" not in str(caught.value)


def test_observer_emits_identifiers_and_outcome_without_payload(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("test-safe-observer")
    observer = JsonLoggingObserver(logger)

    with caplog.at_level(logging.INFO, logger=logger.name):
        observer.record("completed", "msg-1", "corr-1", None, 0.125)

    event = json.loads(caplog.messages[0])
    assert event == {
        "event": "nlp_message_processed",
        "outcome": "completed",
        "message_id": "msg-1",
        "correlation_id": "corr-1",
        "duration_ms": 125.0,
    }
    metric = json.loads(caplog.messages[1])
    assert metric == {
        "event": "nlp_metric",
        "metric": "message_processing_duration_ms",
        "value": 125.0,
        "outcome": "completed",
    }
    assert "msg-1" not in caplog.messages[1]
    assert "corr-1" not in caplog.messages[1]


def test_main_fails_fast_on_invalid_configuration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main({})

    assert exit_code == 2
    captured = capsys.readouterr()
    assert "configuration_invalid" in captured.err
    assert "runtime_started" not in captured.err


def test_shutdown_handler_sets_stop_event(monkeypatch: pytest.MonkeyPatch) -> None:
    handlers: dict[signal.Signals, object] = {}
    monkeypatch.setattr(signal, "signal", handlers.__setitem__)
    stop = Event()

    install_shutdown_handlers(stop)
    handler = handlers[signal.SIGTERM]
    assert callable(handler)
    handler(signal.SIGTERM, None)

    assert stop.is_set()
    assert handlers[signal.SIGINT] is handler


def test_retry_metric_uses_bounded_fields_without_identifiers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("test-retry-observer")
    observer = JsonDeliveryObserver(logger)

    with caplog.at_level(logging.INFO, logger=logger.name):
        observer.record_retry("correlation_unavailable", 1, 2.0)

    event = json.loads(caplog.messages[0])
    assert event == {
        "event": "nlp_metric",
        "metric": "correlation_retry",
        "value": 1,
        "reason": "correlation_unavailable",
        "attempt": 1,
        "delay_ms": 2000.0,
    }
    assert "message_id" not in event
    assert "correlation_id" not in event
