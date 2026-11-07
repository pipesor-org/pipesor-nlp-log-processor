import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class InvalidIntegrationMessage(ValueError):
    """A safe contract error that never includes payload values."""

    def __init__(self, code: str, field_paths: tuple[str, ...] = ()) -> None:
        self.code = code
        self.field_paths = field_paths
        suffix = f" ({', '.join(field_paths)})" if field_paths else ""
        super().__init__(f"{code}{suffix}")


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidIntegrationMessage("invalid_field_type", (path,))
    return value


def _text(container: dict[str, Any], path: str) -> str:
    value: Any = container
    for part in path.split("."):
        value = _object(value, path).get(part)
    if not isinstance(value, str) or not value.strip():
        raise InvalidIntegrationMessage("missing_required_field", (path,))
    return value.strip()


@dataclass(frozen=True, slots=True)
class ProtectedEvidenceMessage:
    schema_version: str
    message_id: str
    occurred_at: str
    correlation_id: str
    source: str
    repository: str
    workflow: str
    execution_id: str
    external_run_id: str
    stage: str
    job: str | None
    summary: str
    excerpt: str
    sequence: int | None

    @classmethod
    def from_json(cls, body: bytes | str) -> "ProtectedEvidenceMessage":
        try:
            decoded = body.decode("utf-8-sig") if isinstance(body, bytes) else body
            payload = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
            raise InvalidIntegrationMessage("invalid_json") from error

        root = _object(payload, "$.")
        schema_version = _text(root, "schema_version")
        if schema_version.split(".", 1)[0] != "1":
            raise InvalidIntegrationMessage("unsupported_schema_version", ("schema_version",))
        if _text(root, "event_type") != "protected_workflow_evidence":
            raise InvalidIntegrationMessage("unsupported_event_type", ("event_type",))

        occurred_at = _text(root, "occurred_at")
        try:
            parsed_time = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            if parsed_time.tzinfo is None:
                raise ValueError
        except ValueError as error:
            raise InvalidIntegrationMessage("invalid_timestamp", ("occurred_at",)) from error

        job = _object(root.get("evidence"), "evidence").get("job")
        if job is not None and (not isinstance(job, str) or not job.strip()):
            raise InvalidIntegrationMessage("invalid_field_type", ("evidence.job",))
        sequence = _object(root.get("evidence"), "evidence").get("sequence")
        if sequence is not None and (not isinstance(sequence, int) or isinstance(sequence, bool)):
            raise InvalidIntegrationMessage("invalid_field_type", ("evidence.sequence",))

        return cls(
            schema_version=schema_version,
            message_id=_text(root, "message_id"),
            occurred_at=occurred_at,
            correlation_id=_text(root, "correlation_id"),
            source=_text(root, "source"),
            repository=_text(root, "pipeline.repository"),
            workflow=_text(root, "pipeline.workflow"),
            execution_id=_text(root, "execution.execution_id"),
            external_run_id=_text(root, "execution.external_run_id"),
            stage=_text(root, "evidence.stage"),
            job=job.strip() if job else None,
            summary=_text(root, "evidence.summary"),
            excerpt=_text(root, "evidence.excerpt"),
            sequence=sequence,
        )


@dataclass(frozen=True, slots=True)
class CorrelationEvidence:
    contract_version: str
    message_id: str
    correlation_id: str
    execution_id: str
    source: str
    observed_at: str
    stage: str
    job: str | None
    sanitized_content: str
    template: str
    traceparent: str | None = None
    tracestate: str | None = None

