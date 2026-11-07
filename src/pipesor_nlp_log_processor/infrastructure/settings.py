from dataclasses import dataclass
from typing import Mapping


class InvalidSettings(ValueError):
    """Raised for missing or invalid non-secret runtime configuration."""


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    correlation_endpoint: str
    correlation_timeout_seconds: float
    correlation_max_attempts: int
    correlation_backoff_seconds: float

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "RuntimeSettings":
        endpoint = environment.get("PIPESOR_CORRELATION_ENDPOINT", "").strip()
        if not endpoint.startswith("https://"):
            raise InvalidSettings("PIPESOR_CORRELATION_ENDPOINT must use HTTPS")
        try:
            timeout = float(environment.get("PIPESOR_CORRELATION_TIMEOUT_SECONDS", "5"))
            attempts = int(environment["PIPESOR_CORRELATION_MAX_ATTEMPTS"])
            backoff = float(environment["PIPESOR_CORRELATION_BACKOFF_SECONDS"])
        except KeyError as error:
            raise InvalidSettings(f"{error.args[0]} is required") from error
        except ValueError as error:
            raise InvalidSettings("Correlation retry settings must be numeric") from error
        if not 0 < timeout <= 60:
            raise InvalidSettings(
                "PIPESOR_CORRELATION_TIMEOUT_SECONDS must be between 0 and 60"
            )
        if not 1 <= attempts <= 10:
            raise InvalidSettings(
                "PIPESOR_CORRELATION_MAX_ATTEMPTS must be between 1 and 10"
            )
        if not 0 <= backoff <= 30:
            raise InvalidSettings(
                "PIPESOR_CORRELATION_BACKOFF_SECONDS must be between 0 and 30"
            )
        return cls(endpoint.rstrip("/"), timeout, attempts, backoff)

