import json
import re
import uuid
from urllib.parse import unquote_to_bytes
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


EVENT_TYPE = "protected_workflow_evidence"
SCHEMA_VERSION = "2.0"
SCHEMA_MAJOR = 2
SOURCE = "github_actions"
STAGES = frozenset(
    {
        "BUILD",
        "TESTING",
        "QUALITY_GATE",
        "DEPLOYMENT",
        "CONTAINER",
        "INFRASTRUCTURE",
    }
)
_SAFE_CONTEXT = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_EXTERNAL_ID = re.compile(r"[1-9][0-9]*")
_COMMIT_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_LOCATOR = re.compile(
    r"^v2:job:([1-9][0-9]{0,19}):member:"
    r"((?:[A-Za-z0-9._~-]|%[0-9A-F]{2}){1,765}):lines:"
    r"([1-9][0-9]{0,8})-([1-9][0-9]{0,8}):sha256:[0-9a-f]{64}$"
)


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


def _value(container: dict[str, Any], path: str) -> Any:
    value: Any = container
    for part in path.split("."):
        current = _object(value, path)
        if part not in current:
            raise InvalidIntegrationMessage("missing_required_field", (path,))
        value = current[part]
    return value


def _text(container: dict[str, Any], path: str) -> str:
    value = _value(container, path)
    if not isinstance(value, str) or not value.strip():
        raise InvalidIntegrationMessage("missing_required_field", (path,))
    return value.strip()


def _nullable_text(container: dict[str, Any], path: str) -> str | None:
    value = _value(container, path)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InvalidIntegrationMessage("invalid_field_type", (path,))
    return value.strip()


def _optional_text(container: dict[str, Any], name: str, path: str) -> str | None:
    value = container.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InvalidIntegrationMessage("invalid_field_type", (path,))
    return value.strip()


def _integer(container: dict[str, Any], path: str, minimum: int) -> int:
    value = _value(container, path)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise InvalidIntegrationMessage("invalid_field_type", (path,))
    return value


def _optional_integer(container: dict[str, Any], name: str, path: str) -> int | None:
    value = container.get(name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InvalidIntegrationMessage("invalid_field_type", (path,))
    return value


def _canonical_uuid(container: dict[str, Any], path: str) -> str:
    value = _text(container, path)
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise InvalidIntegrationMessage("invalid_identifier", (path,)) from error
    if parsed.int == 0 or str(parsed) != value:
        raise InvalidIntegrationMessage("invalid_identifier", (path,))
    return value


def _external_id(container: dict[str, Any], path: str) -> str:
    value = _text(container, path)
    if not _EXTERNAL_ID.fullmatch(value):
        raise InvalidIntegrationMessage("invalid_identifier", (path,))
    return value


def _timestamp(container: dict[str, Any], path: str) -> str:
    value = _text(container, path)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise InvalidIntegrationMessage("invalid_timestamp", (path,)) from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise InvalidIntegrationMessage("invalid_timestamp", (path,))
    return value


def _logical_id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def _source_locator(value: object, job: str | None) -> str:
    if not isinstance(value, str) or job is None:
        raise InvalidIntegrationMessage("invalid_identifier", ("evidence.source_locator",))
    match = _LOCATOR.fullmatch(value)
    if match is None or match.group(1) != job:
        raise InvalidIntegrationMessage("invalid_identifier", ("evidence.source_locator",))
    try:
        member_bytes = unquote_to_bytes(match.group(2))
        member = member_bytes.decode("utf-8")
    except (UnicodeDecodeError, ValueError) as error:
        raise InvalidIntegrationMessage("invalid_identifier", ("evidence.source_locator",)) from error
    parts = member.split("/")
    first, last = int(match.group(3)), int(match.group(4))
    if (
        not 1 <= len(member_bytes) <= 255
        or member.startswith("/")
        or "\\" in member
        or len(parts) > 2
        or ".." in parts
        or any(ord(character) < 32 or ord(character) == 127 for character in member)
        or first > last
        or last - first > 199
    ):
        raise InvalidIntegrationMessage("invalid_identifier", ("evidence.source_locator",))
    return value


def _source_locator_v21(value: object, job: str | None) -> str:
    if (
        not isinstance(value, str)
        or not job
        or not _EXTERNAL_ID.fullmatch(job)
        or not re.fullmatch(
            rf"v2:job:{job}:member:[0-9]{{1,9}}_verify\.txt:lines:([1-9][0-9]{{0,8}})-\1:sha256:[0-9a-f]{{64}}",
            value,
        )
    ):
        raise InvalidIntegrationMessage("invalid_identifier", ("evidence.source_locator",))
    return value


@dataclass(frozen=True, slots=True)
class CaptureContext:
    tenant_id: str
    github_account_id: str
    github_installation_id: str
    scope_revision: str


@dataclass(frozen=True, slots=True)
class PipelineContext:
    github_repository_id: str
    github_workflow_id: str


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    github_run_id: str
    run_attempt: int
    status: str
    conclusion: str


@dataclass(frozen=True, slots=True)
class CommitContext:
    sha: str


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    stage: str
    job: str | None
    summary: str
    excerpt: str
    sequence: int | None
    source_locator: str | None = None


@dataclass(frozen=True, slots=True)
class ProtectedEvidenceMessage:
    schema_version: str
    event_type: str
    message_id: str
    occurred_at: str
    source_updated_at: str
    correlation_id: str
    source: str
    capture_context: CaptureContext
    pipeline: PipelineContext
    execution: ExecutionContext
    commit: CommitContext
    service: str | None
    environment: str | None
    evidence: EvidenceContext

    @property
    def pipeline_id(self) -> str:
        return _logical_id(
            "urn:pipesor:v1:pipeline:github:"
            f"{self.pipeline.github_repository_id}:{self.pipeline.github_workflow_id}"
        )

    @property
    def execution_id(self) -> str:
        return _logical_id(
            "urn:pipesor:v1:execution:github:"
            f"{self.pipeline.github_repository_id}:{self.execution.github_run_id}:"
            f"{self.execution.run_attempt}"
        )

    @property
    def incident_id(self) -> str:
        return _logical_id(f"urn:pipesor:v1:incident:{self.execution_id}")

    @property
    def evidence_id(self) -> str:
        return _logical_id(f"urn:pipesor:v1:evidence:{self.message_id}")

    @classmethod
    def from_json(cls, body: bytes | str) -> "ProtectedEvidenceMessage":
        try:
            decoded = body.decode("utf-8-sig") if isinstance(body, bytes) else body
            payload = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
            raise InvalidIntegrationMessage("invalid_json") from error

        root = _object(payload, "$")
        schema_version = _text(root, "schema_version")
        if schema_version not in {SCHEMA_VERSION, "2.1", "2.2"}:
            raise InvalidIntegrationMessage(
                "unsupported_schema_version", ("schema_version",)
            )
        event_type = _text(root, "event_type")
        if event_type != EVENT_TYPE:
            raise InvalidIntegrationMessage("unsupported_event_type", ("event_type",))

        source = _text(root, "source")
        if source != SOURCE:
            raise InvalidIntegrationMessage("unsupported_source", ("source",))
        correlation_id = _text(root, "correlation_id")
        if not _SAFE_CONTEXT.fullmatch(correlation_id):
            raise InvalidIntegrationMessage("invalid_field_value", ("correlation_id",))

        capture = _object(_value(root, "capture_context"), "capture_context")
        scope_revision = _text(capture, "scope_revision")
        if not _SAFE_CONTEXT.fullmatch(scope_revision):
            raise InvalidIntegrationMessage(
                "invalid_field_value", ("capture_context.scope_revision",)
            )
        pipeline = _object(_value(root, "pipeline"), "pipeline")
        execution = _object(_value(root, "execution"), "execution")
        commit = _object(_value(root, "commit"), "commit")
        evidence = _object(_value(root, "evidence"), "evidence")

        locator = evidence.get("source_locator")
        job = _optional_text(evidence, "job", "evidence.job")
        if schema_version == "2.1":
            locator = _source_locator_v21(locator, job)
        elif schema_version == "2.2":
            locator = _source_locator(locator, job)
        elif "source_locator" in evidence:
            raise InvalidIntegrationMessage("invalid_identifier", ("evidence.source_locator",))

        status = _text(execution, "status")
        conclusion = _text(execution, "conclusion")
        if status != "completed" or conclusion not in {"failure", "timed_out"}:
            raise InvalidIntegrationMessage(
                "unsupported_execution_outcome", ("execution",)
            )
        stage = _text(evidence, "stage")
        if stage not in STAGES:
            raise InvalidIntegrationMessage("unsupported_stage", ("evidence.stage",))
        sha = _text(commit, "sha")
        if not _COMMIT_SHA.fullmatch(sha):
            raise InvalidIntegrationMessage("invalid_identifier", ("commit.sha",))

        return cls(
            schema_version=schema_version,
            event_type=event_type,
            message_id=_canonical_uuid(root, "message_id"),
            occurred_at=_timestamp(root, "occurred_at"),
            source_updated_at=_timestamp(root, "source_updated_at"),
            correlation_id=correlation_id,
            source=source,
            capture_context=CaptureContext(
                tenant_id=_canonical_uuid(capture, "tenant_id"),
                github_account_id=_external_id(capture, "github_account_id"),
                github_installation_id=_external_id(
                    capture, "github_installation_id"
                ),
                scope_revision=scope_revision,
            ),
            pipeline=PipelineContext(
                github_repository_id=_external_id(pipeline, "github_repository_id"),
                github_workflow_id=_external_id(pipeline, "github_workflow_id"),
            ),
            execution=ExecutionContext(
                github_run_id=_external_id(execution, "github_run_id"),
                run_attempt=_integer(execution, "run_attempt", 1),
                status=status,
                conclusion=conclusion,
            ),
            commit=CommitContext(sha),
            service=_nullable_text(root, "service"),
            environment=_nullable_text(root, "environment"),
            evidence=EvidenceContext(
                stage=stage,
                job=job,
                source_locator=locator,
                summary=_text(evidence, "summary"),
                excerpt=_text(evidence, "excerpt"),
                sequence=_optional_integer(evidence, "sequence", "evidence.sequence"),
            ),
        )


@dataclass(frozen=True, slots=True)
class CorrelationEvidence:
    contract_version: str
    message_id: str
    correlation_id: str
    source: str
    occurred_at: str
    source_updated_at: str
    capture_context: CaptureContext
    pipeline: PipelineContext
    execution: ExecutionContext
    commit: CommitContext
    service: str | None
    environment: str | None
    pipeline_id: str
    execution_id: str
    incident_id: str
    evidence_id: str
    stage: str
    job: str | None
    sequence: int | None
    sanitized_summary: str
    sanitized_content: str
    template: str
    source_locator: str | None = None
    traceparent: str | None = None
    tracestate: str | None = None
