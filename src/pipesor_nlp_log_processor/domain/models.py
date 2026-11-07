from dataclasses import dataclass


class InvalidLogEnvelope(ValueError):
    """Raised when required processing context is missing."""


@dataclass(frozen=True, slots=True)
class LogEnvelope:
    content: str
    correlation_id: str
    execution_id: str
    source: str

    def __post_init__(self) -> None:
        for field_name in ("content", "correlation_id", "execution_id", "source"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise InvalidLogEnvelope(f"{field_name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ParsedTemplate:
    cluster_id: int
    template: str


@dataclass(frozen=True, slots=True)
class ProcessedLog:
    sanitized_content: str
    correlation_id: str
    execution_id: str
    source: str
    cluster_id: int
    template: str

