from dataclasses import dataclass
from typing import Mapping


class InvalidSettings(ValueError):
    """Raised for missing or invalid non-secret runtime configuration."""


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    correlation_endpoint: str
    correlation_timeout_seconds: float

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "RuntimeSettings":
        endpoint = environment.get("PIPESOR_CORRELATION_ENDPOINT", "").strip()
        if not endpoint.startswith("https://"):
            raise InvalidSettings("PIPESOR_CORRELATION_ENDPOINT must use HTTPS")
        try:
            timeout = float(environment.get("PIPESOR_CORRELATION_TIMEOUT_SECONDS", "5"))
        except ValueError as error:
            raise InvalidSettings(
                "PIPESOR_CORRELATION_TIMEOUT_SECONDS must be numeric"
            ) from error
        if not 0 < timeout <= 60:
            raise InvalidSettings(
                "PIPESOR_CORRELATION_TIMEOUT_SECONDS must be between 0 and 60"
            )
        return cls(endpoint.rstrip("/"), timeout)

