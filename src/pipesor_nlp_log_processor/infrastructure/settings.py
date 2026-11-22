from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping
from urllib.parse import urlsplit
from uuid import UUID


class InvalidSettings(ValueError):
    """Raised for missing or invalid non-secret runtime configuration."""


def _number(environment: Mapping[str, str], name: str, default: str, minimum: float, maximum: float) -> float:
    try:
        value = float(environment.get(name, default))
    except ValueError as error:
        raise InvalidSettings(f"{name} must be numeric") from error
    if not minimum <= value <= maximum:
        raise InvalidSettings(f"{name} must be between {minimum} and {maximum}")
    return value


def _endpoint(value: str, local: bool) -> str:
    parsed = urlsplit(value)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise InvalidSettings("PIPESOR_ENDPOINTS_CORRELATION must be an absolute endpoint")
    if parsed.scheme == "https":
        return value.rstrip("/")
    if parsed.scheme == "http" and local and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return value.rstrip("/")
    raise InvalidSettings("HTTPS is required except for local loopback endpoints")


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    environment: Literal["local", "azure"]
    tenant_id: UUID
    workload_id: str
    signing_key_file: Path | None
    signing_key_id: str | None
    server_address: str
    server_port: int
    service_bus_connection_string: str | None
    service_bus_namespace: str | None
    service_bus_topic: str
    service_bus_subscription: str
    service_bus_receive_wait_seconds: float
    service_bus_max_lock_renewal_seconds: float
    correlation_endpoint: str
    correlation_timeout_seconds: float
    correlation_total_timeout_seconds: float
    correlation_max_attempts: int
    correlation_backoff_seconds: float
    termination_grace_seconds: float
    log_level: str

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "RuntimeSettings":
        profile = environment.get("PIPESOR_ENVIRONMENT", "").strip()
        if profile not in {"local", "azure"}:
            raise InvalidSettings("PIPESOR_ENVIRONMENT must be local or azure")
        try:
            tenant = UUID(environment.get("PIPESOR_TENANT_ID", ""))
        except ValueError as error:
            raise InvalidSettings("PIPESOR_TENANT_ID must be a UUID") from error
        if environment.get("PIPESOR_WORKLOAD_ID", "").strip() != "nlp":
            raise InvalidSettings("PIPESOR_WORKLOAD_ID must be nlp")
        key_file_value = environment.get("PIPESOR_SIGNING_KEY_FILE", "").strip()
        key_id = environment.get("PIPESOR_SIGNING_KEY_ID", "").strip() or None
        if profile == "local":
            if not key_file_value or key_id:
                raise InvalidSettings("local requires only PIPESOR_SIGNING_KEY_FILE")
            key_file = Path(key_file_value)
        else:
            if key_file_value or not key_id:
                raise InvalidSettings("azure requires only PIPESOR_SIGNING_KEY_ID")
            key_file = None
        address = environment.get("SERVER_ADDRESS", "").strip()
        expected = "127.0.0.1" if profile == "local" else "0.0.0.0"
        if address != expected:
            raise InvalidSettings(f"SERVER_ADDRESS must be {expected} for {profile}")
        try:
            port = int(environment.get("SERVER_PORT", ""))
        except ValueError as error:
            raise InvalidSettings("SERVER_PORT must be numeric") from error
        if not 1 <= port <= 65535:
            raise InvalidSettings("SERVER_PORT is invalid")
        connection = environment.get("PIPESOR_SERVICE_BUS_CONNECTION_STRING", "").strip() or None
        namespace = environment.get("PIPESOR_SERVICE_BUS_NAMESPACE", "").strip() or None
        if profile == "local":
            if not connection or namespace:
                raise InvalidSettings("local requires only PIPESOR_SERVICE_BUS_CONNECTION_STRING")
        elif connection or not namespace or not namespace.endswith(".servicebus.windows.net") or "://" in namespace:
            raise InvalidSettings("azure requires only a fully qualified PIPESOR_SERVICE_BUS_NAMESPACE")
        topic = environment.get("PIPESOR_SERVICE_BUS_TOPIC", "").strip()
        subscription = environment.get("PIPESOR_SERVICE_BUS_SUBSCRIPTION", "").strip()
        if topic != "workflow-evidence" or subscription != "nlp":
            raise InvalidSettings("Service Bus topic/subscription must be workflow-evidence/nlp")
        endpoint = _endpoint(environment.get("PIPESOR_ENDPOINTS_CORRELATION", "").strip(), profile == "local")
        log_level = environment.get("PIPESOR_LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise InvalidSettings("PIPESOR_LOG_LEVEL is invalid")
        receive_wait = _number(environment, "PIPESOR_SERVICE_BUS_RECEIVE_WAIT_SECONDS", "5", 0.001, 300)
        lock_renewal = _number(environment, "PIPESOR_SERVICE_BUS_MAX_LOCK_RENEWAL_SECONDS", "120", 30, 3600)
        timeout = _number(environment, "PIPESOR_CORRELATION_TIMEOUT_SECONDS", "5", 0.001, 60)
        total_timeout = _number(environment, "PIPESOR_CORRELATION_TOTAL_TIMEOUT_SECONDS", "40", timeout, 300)
        backoff = _number(environment, "PIPESOR_CORRELATION_BACKOFF_SECONDS", "1", 0, 30)
        termination = _number(environment, "PIPESOR_TERMINATION_GRACE_SECONDS", "60", 30, 300)
        try:
            attempts = int(environment.get("PIPESOR_CORRELATION_MAX_ATTEMPTS", "3"))
        except ValueError as error:
            raise InvalidSettings("PIPESOR_CORRELATION_MAX_ATTEMPTS must be numeric") from error
        if not 1 <= attempts <= 10:
            raise InvalidSettings("PIPESOR_CORRELATION_MAX_ATTEMPTS must be between 1 and 10")
        if receive_wait + total_timeout + 10 > termination:
            raise InvalidSettings("Termination grace must cover receive, delivery and cleanup")
        return cls(profile, tenant, "nlp", key_file, key_id, address, port, connection, namespace,
                   topic, subscription, receive_wait, lock_renewal, endpoint, timeout, total_timeout,
                   attempts, backoff, termination, log_level)
