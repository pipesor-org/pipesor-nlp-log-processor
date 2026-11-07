from dataclasses import dataclass
from typing import Mapping


class InvalidSettings(ValueError):
    """Raised for missing or invalid non-secret runtime configuration."""


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    service_bus_namespace: str
    service_bus_topic: str
    service_bus_subscription: str
    service_bus_receive_wait_seconds: float
    service_bus_max_lock_renewal_seconds: float
    correlation_endpoint: str
    correlation_scope: str
    correlation_timeout_seconds: float
    correlation_max_attempts: int
    correlation_backoff_seconds: float
    log_level: str

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "RuntimeSettings":
        namespace = environment.get("PIPESOR_SERVICE_BUS_NAMESPACE", "").strip()
        topic = environment.get("PIPESOR_SERVICE_BUS_TOPIC", "").strip()
        subscription = environment.get("PIPESOR_SERVICE_BUS_SUBSCRIPTION", "").strip()
        if not namespace.endswith(".servicebus.windows.net") or "://" in namespace:
            raise InvalidSettings(
                "PIPESOR_SERVICE_BUS_NAMESPACE must be a fully qualified Azure namespace"
            )
        if not topic:
            raise InvalidSettings("PIPESOR_SERVICE_BUS_TOPIC is required")
        if not subscription:
            raise InvalidSettings("PIPESOR_SERVICE_BUS_SUBSCRIPTION is required")
        endpoint = environment.get("PIPESOR_CORRELATION_ENDPOINT", "").strip()
        if not endpoint.startswith("https://"):
            raise InvalidSettings("PIPESOR_CORRELATION_ENDPOINT must use HTTPS")
        scope = environment.get("PIPESOR_CORRELATION_SCOPE", "").strip()
        if not scope.endswith("/.default") or any(character.isspace() for character in scope):
            raise InvalidSettings(
                "PIPESOR_CORRELATION_SCOPE must be a single /.default scope"
            )
        log_level = environment.get("PIPESOR_LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise InvalidSettings("PIPESOR_LOG_LEVEL is invalid")
        try:
            timeout = float(environment.get("PIPESOR_CORRELATION_TIMEOUT_SECONDS", "5"))
            receive_wait = float(environment["PIPESOR_SERVICE_BUS_RECEIVE_WAIT_SECONDS"])
            lock_renewal = float(
                environment["PIPESOR_SERVICE_BUS_MAX_LOCK_RENEWAL_SECONDS"]
            )
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
        if not 0 < receive_wait <= 300:
            raise InvalidSettings(
                "PIPESOR_SERVICE_BUS_RECEIVE_WAIT_SECONDS must be between 0 and 300"
            )
        if not 30 <= lock_renewal <= 3600:
            raise InvalidSettings(
                "PIPESOR_SERVICE_BUS_MAX_LOCK_RENEWAL_SECONDS must be between 30 and 3600"
            )
        if not 1 <= attempts <= 10:
            raise InvalidSettings(
                "PIPESOR_CORRELATION_MAX_ATTEMPTS must be between 1 and 10"
            )
        if not 0 <= backoff <= 30:
            raise InvalidSettings(
                "PIPESOR_CORRELATION_BACKOFF_SECONDS must be between 0 and 30"
            )
        return cls(
            namespace,
            topic,
            subscription,
            receive_wait,
            lock_renewal,
            endpoint.rstrip("/"),
            scope,
            timeout,
            attempts,
            backoff,
            log_level,
        )

