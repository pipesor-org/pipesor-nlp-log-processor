import json
import logging

import pytest

from pipesor_nlp_log_processor.application.integration_ports import TransientDeliveryError
from pipesor_nlp_log_processor.infrastructure.correlation_http import AzureCredentialTokenProvider
from pipesor_nlp_log_processor.infrastructure.observability import JsonLoggingObserver
from pipesor_nlp_log_processor.main import main


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
        observer.record("completed", "msg-1", "corr-1", None)

    event = json.loads(caplog.messages[0])
    assert event == {
        "event": "nlp_message_processed",
        "outcome": "completed",
        "message_id": "msg-1",
        "correlation_id": "corr-1",
    }


def test_main_fails_fast_on_invalid_configuration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main({})

    assert exit_code == 2
    captured = capsys.readouterr()
    assert "configuration_invalid" in captured.err
    assert "runtime_started" not in captured.err
