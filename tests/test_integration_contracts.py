import json
from pathlib import Path

import pytest

from pipesor_nlp_log_processor import (
    DeliveryStatus,
    DrainLogParser,
    PatternLogSanitizer,
    ProcessEvidence,
    ProcessLog,
    ReceivedMessage,
)
from pipesor_nlp_log_processor.domain.integration import InvalidIntegrationMessage
from pipesor_nlp_log_processor.infrastructure.settings import InvalidSettings, RuntimeSettings
from pipesor_nlp_log_processor.infrastructure.simulated import RecordingEvidenceDelivery


def payload(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "2.0",
        "event_type": "protected_workflow_evidence",
        "message_id": "cf6b69cf-a28d-49f3-bceb-000000000001",
        "occurred_at": "2026-11-07T07:30:00Z",
        "source_updated_at": "2026-11-07T07:31:00Z",
        "correlation_id": "corr-1",
        "source": "github_actions",
        "capture_context": {
            "tenant_id": "d91d1df6-ae89-40c2-99b5-20edfb3e9ed0",
            "github_account_id": "68021",
            "github_installation_id": "77106",
            "scope_revision": "scope-rev-12",
        },
        "pipeline": {
            "github_repository_id": "73109",
            "github_workflow_id": "4802",
        },
        "execution": {
            "github_run_id": "9920081",
            "run_attempt": 3,
            "status": "completed",
            "conclusion": "failure",
        },
        "commit": {"sha": "a17c4db0123456789abcdef0123456789abcdef0"},
        "service": None,
        "environment": None,
        "evidence": {
            "stage": "BUILD",
            "job": "test",
            "summary": 'Build failed password="summary secret with spaces"',
            "excerpt": 'request failed {"token": "excerpt secret with spaces"}',
            "sequence": 3,
        },
    }
    value.update(overrides)
    return value


def received(value: dict[str, object] | None = None) -> ReceivedMessage:
    return ReceivedMessage(
        body=json.dumps(value or payload()).encode(),
        message_id="cf6b69cf-a28d-49f3-bceb-000000000001",
        correlation_id="corr-1",
        broker_event_type="protected_workflow_evidence",
        broker_schema_major=2,
    )


def handler(delivery: RecordingEvidenceDelivery) -> ProcessEvidence:
    return ProcessEvidence(
        ProcessLog(DrainLogParser(), PatternLogSanitizer()), delivery
    )


def test_maps_sanitized_message_to_correlation_contract() -> None:
    delivery = RecordingEvidenceDelivery()

    result = handler(delivery).execute(received())

    assert result is DeliveryStatus.ACCEPTED
    assert len(delivery.deliveries) == 1
    evidence = delivery.deliveries[0]
    assert evidence.contract_version == "2.0"
    assert evidence.message_id == "cf6b69cf-a28d-49f3-bceb-000000000001"
    assert evidence.correlation_id == "corr-1"
    assert evidence.pipeline_id == "b563bc93-451f-5c62-9219-9e112a6ea48e"
    assert evidence.execution_id == "1acbfca9-f346-5a6f-9851-2cf96f393c86"
    assert evidence.incident_id == "11e7c76b-d031-595a-beb3-4073760e5793"
    assert evidence.evidence_id == "e6988490-6322-590d-b48d-662b154b55d9"
    assert evidence.capture_context.tenant_id == "d91d1df6-ae89-40c2-99b5-20edfb3e9ed0"
    assert evidence.execution.run_attempt == 3
    assert evidence.commit.sha == "a17c4db0123456789abcdef0123456789abcdef0"
    assert evidence.stage == "BUILD"
    assert "summary secret" not in evidence.sanitized_summary
    assert "excerpt secret" not in evidence.sanitized_content
    assert "cluster" not in evidence.__dataclass_fields__


def test_accepts_idempotent_duplicate_delivery_result() -> None:
    delivery = RecordingEvidenceDelivery(DeliveryStatus.DUPLICATE)
    assert handler(delivery).execute(received()) is DeliveryStatus.DUPLICATE


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (b"not-json", "invalid_json"),
        (json.dumps({**payload(), "schema_version": "1.0"}).encode(), "unsupported_schema_version"),
    ],
)
def test_rejects_invalid_messages_without_echoing_values(value: bytes, code: str) -> None:
    message = ReceivedMessage(value, "msg-1", "corr-1")
    with pytest.raises(InvalidIntegrationMessage, match=code) as caught:
        handler(RecordingEvidenceDelivery()).execute(message)
    assert "not-json" not in str(caught.value)


def test_rejects_broker_property_mismatch() -> None:
    message = ReceivedMessage(
        received().body,
        "different",
        "corr-1",
        broker_event_type="protected_workflow_evidence",
        broker_schema_major=2,
    )
    with pytest.raises(InvalidIntegrationMessage, match="broker_property_mismatch") as caught:
        handler(RecordingEvidenceDelivery()).execute(message)
    assert caught.value.field_paths == ("message_id",)


def test_rejects_filter_property_mismatch_with_body() -> None:
    message = ReceivedMessage(
        received().body,
        "cf6b69cf-a28d-49f3-bceb-000000000001",
        "corr-1",
        broker_event_type="different_event",
        broker_schema_major=2,
    )

    with pytest.raises(InvalidIntegrationMessage, match="broker_property_mismatch") as caught:
        handler(RecordingEvidenceDelivery()).execute(message)

    assert caught.value.field_paths == ("eventType",)


def test_requires_v2_broker_properties_at_application_boundary() -> None:
    message = ReceivedMessage(
        received().body,
        "cf6b69cf-a28d-49f3-bceb-000000000001",
        "corr-1",
    )

    with pytest.raises(InvalidIntegrationMessage, match="broker_property_mismatch") as caught:
        handler(RecordingEvidenceDelivery()).execute(message)

    assert caught.value.field_paths == ("eventType", "schemaMajor")


def test_accepts_ingestion_generated_v2_fixture_and_preserves_context() -> None:
    fixture = Path(__file__).parent / "fixtures" / "ingestion-evidence-v2.json"
    delivery = RecordingEvidenceDelivery()
    message = ReceivedMessage(
        fixture.read_bytes(),
        "cf6b69cf-a28d-49f3-bceb-000000000001",
        "ci-investigation-204",
        broker_event_type="protected_workflow_evidence",
        broker_schema_major=2,
    )

    assert handler(delivery).execute(message) is DeliveryStatus.ACCEPTED
    evidence = delivery.deliveries[0]
    assert evidence.pipeline_id == "b2e4adde-f9ef-54c8-b00f-7914af19efde"
    assert evidence.execution_id == "9306afb1-dafe-5915-bded-b9ba5237985a"
    assert evidence.incident_id == "6b14d1b6-fce1-5fe1-8286-8ae6977b3dd0"
    assert evidence.evidence_id == "e6988490-6322-590d-b48d-662b154b55d9"
    assert evidence.source_updated_at == "2026-11-08T09:20:40.987654321Z"
    assert evidence.pipeline.github_repository_id == "92233720368547758081234567"


def test_rejects_unapproved_stage() -> None:
    invalid = payload()
    invalid["evidence"] = {**invalid["evidence"], "stage": "UNAPPROVED_STAGE"}  # type: ignore[arg-type]
    with pytest.raises(InvalidIntegrationMessage, match="unsupported_stage"):
        handler(RecordingEvidenceDelivery()).execute(received(invalid))


def test_loads_typed_runtime_settings() -> None:
    settings = RuntimeSettings.from_environment(
        {
            "PIPESOR_SERVICE_BUS_NAMESPACE": "pipesor-dev.servicebus.windows.net",
            "PIPESOR_SERVICE_BUS_TOPIC": "protected-evidence",
            "PIPESOR_SERVICE_BUS_SUBSCRIPTION": "nlp-log-processor",
            "PIPESOR_SERVICE_BUS_RECEIVE_WAIT_SECONDS": "10",
            "PIPESOR_SERVICE_BUS_MAX_LOCK_RENEWAL_SECONDS": "300",
            "PIPESOR_CORRELATION_ENDPOINT": "https://correlation.internal/",
            "PIPESOR_CORRELATION_SCOPE": "api://correlation/.default",
            "PIPESOR_CORRELATION_TIMEOUT_SECONDS": "2.5",
            "PIPESOR_CORRELATION_TOTAL_TIMEOUT_SECONDS": "35",
            "PIPESOR_CORRELATION_MAX_ATTEMPTS": "3",
            "PIPESOR_CORRELATION_BACKOFF_SECONDS": "0.25",
            "PIPESOR_TERMINATION_GRACE_SECONDS": "60",
        }
    )
    assert settings.correlation_endpoint == "https://correlation.internal"
    assert settings.correlation_scope == "api://correlation/.default"
    assert settings.correlation_timeout_seconds == 2.5
    assert settings.correlation_total_timeout_seconds == 35
    assert settings.correlation_max_attempts == 3
    assert settings.correlation_backoff_seconds == 0.25
    assert settings.service_bus_namespace == "pipesor-dev.servicebus.windows.net"
    assert settings.service_bus_receive_wait_seconds == 10
    assert settings.service_bus_max_lock_renewal_seconds == 300
    assert settings.termination_grace_seconds == 60


def test_rejects_insecure_or_unbounded_runtime_settings() -> None:
    with pytest.raises(InvalidSettings, match="HTTPS"):
        RuntimeSettings.from_environment(
            {
                "PIPESOR_SERVICE_BUS_NAMESPACE": "pipesor-dev.servicebus.windows.net",
                "PIPESOR_SERVICE_BUS_TOPIC": "protected-evidence",
                "PIPESOR_SERVICE_BUS_SUBSCRIPTION": "nlp-log-processor",
                "PIPESOR_SERVICE_BUS_RECEIVE_WAIT_SECONDS": "10",
                "PIPESOR_SERVICE_BUS_MAX_LOCK_RENEWAL_SECONDS": "300",
                "PIPESOR_CORRELATION_ENDPOINT": "http://correlation.internal",
                "PIPESOR_CORRELATION_SCOPE": "api://correlation/.default",
                "PIPESOR_CORRELATION_MAX_ATTEMPTS": "3",
                "PIPESOR_CORRELATION_BACKOFF_SECONDS": "1",
                "PIPESOR_CORRELATION_TOTAL_TIMEOUT_SECONDS": "35",
                "PIPESOR_TERMINATION_GRACE_SECONDS": "60",
            }
        )
    with pytest.raises(InvalidSettings, match="between 0 and 60"):
        RuntimeSettings.from_environment(
            {
                "PIPESOR_SERVICE_BUS_NAMESPACE": "pipesor-dev.servicebus.windows.net",
                "PIPESOR_SERVICE_BUS_TOPIC": "protected-evidence",
                "PIPESOR_SERVICE_BUS_SUBSCRIPTION": "nlp-log-processor",
                "PIPESOR_SERVICE_BUS_RECEIVE_WAIT_SECONDS": "10",
                "PIPESOR_SERVICE_BUS_MAX_LOCK_RENEWAL_SECONDS": "300",
                "PIPESOR_CORRELATION_ENDPOINT": "https://correlation.internal",
                "PIPESOR_CORRELATION_SCOPE": "api://correlation/.default",
                "PIPESOR_CORRELATION_TIMEOUT_SECONDS": "0",
                "PIPESOR_CORRELATION_TOTAL_TIMEOUT_SECONDS": "35",
                "PIPESOR_CORRELATION_MAX_ATTEMPTS": "3",
                "PIPESOR_CORRELATION_BACKOFF_SECONDS": "1",
                "PIPESOR_TERMINATION_GRACE_SECONDS": "60",
            }
        )


def test_validation_budget_fits_sixty_second_termination_grace() -> None:
    settings = RuntimeSettings.from_environment(
        {
            "PIPESOR_SERVICE_BUS_NAMESPACE": "pipesor-dev.servicebus.windows.net",
            "PIPESOR_SERVICE_BUS_TOPIC": "protected-workflow-evidence",
            "PIPESOR_SERVICE_BUS_SUBSCRIPTION": "nlp-log-processor",
            "PIPESOR_SERVICE_BUS_RECEIVE_WAIT_SECONDS": "5",
            "PIPESOR_SERVICE_BUS_MAX_LOCK_RENEWAL_SECONDS": "300",
            "PIPESOR_CORRELATION_ENDPOINT": "https://correlation.internal",
            "PIPESOR_CORRELATION_SCOPE": "api://correlation/.default",
            "PIPESOR_CORRELATION_TIMEOUT_SECONDS": "10",
            "PIPESOR_CORRELATION_TOTAL_TIMEOUT_SECONDS": "40",
            "PIPESOR_CORRELATION_MAX_ATTEMPTS": "3",
            "PIPESOR_CORRELATION_BACKOFF_SECONDS": "1",
            "PIPESOR_TERMINATION_GRACE_SECONDS": "60",
        }
    )

    assert settings.service_bus_receive_wait_seconds == 5
    assert settings.correlation_total_timeout_seconds == 40
    assert settings.termination_grace_seconds == 60


def test_rejects_shutdown_budget_that_exceeds_termination_grace() -> None:
    with pytest.raises(InvalidSettings, match="Termination grace"):
        RuntimeSettings.from_environment(
            {
                "PIPESOR_SERVICE_BUS_NAMESPACE": "pipesor-dev.servicebus.windows.net",
                "PIPESOR_SERVICE_BUS_TOPIC": "protected-workflow-evidence",
                "PIPESOR_SERVICE_BUS_SUBSCRIPTION": "nlp-log-processor",
                "PIPESOR_SERVICE_BUS_RECEIVE_WAIT_SECONDS": "30",
                "PIPESOR_SERVICE_BUS_MAX_LOCK_RENEWAL_SECONDS": "300",
                "PIPESOR_CORRELATION_ENDPOINT": "https://correlation.internal",
                "PIPESOR_CORRELATION_SCOPE": "api://correlation/.default",
                "PIPESOR_CORRELATION_TIMEOUT_SECONDS": "10",
                "PIPESOR_CORRELATION_TOTAL_TIMEOUT_SECONDS": "40",
                "PIPESOR_CORRELATION_MAX_ATTEMPTS": "3",
                "PIPESOR_CORRELATION_BACKOFF_SECONDS": "1",
                "PIPESOR_TERMINATION_GRACE_SECONDS": "60",
            }
        )
