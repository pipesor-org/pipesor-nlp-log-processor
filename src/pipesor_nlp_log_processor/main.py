import json
import logging
import os
import signal
from collections.abc import Mapping
from threading import Event
from threading import Thread
from typing import Any

from azure.identity import DefaultAzureCredential
import uvicorn

from .application.message_loop import ConsumptionPaused, MessageLoop
from .application.process_evidence import ProcessEvidence
from .application.process_log import ProcessLog
from .infrastructure.correlation_http import (
    CorrelationHttpDelivery,
    RetryPolicy,
)
from .infrastructure.drain_parser import DrainLogParser
from .infrastructure.observability import JsonDeliveryObserver, JsonLoggingObserver
from .infrastructure.sanitizer import PatternLogSanitizer
from .infrastructure.service_bus import AzureServiceBusAdapter
from .infrastructure.settings import InvalidSettings, RuntimeSettings
from .infrastructure.health_api import RuntimeHealth, create_health_app
from .infrastructure.platform_identity import KeyVaultSigningKey, PemSigningKey, WorkloadTokenProvider


LOGGER = logging.getLogger("pipesor_nlp_log_processor")


def _event(name: str, **fields: str) -> str:
    return json.dumps({"event": name, **fields}, separators=(",", ":"))


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level), format="%(message)s", force=True)


def install_shutdown_handlers(stop: Event) -> None:
    def request_stop(signum: int, _: object) -> None:
        LOGGER.info(_event("shutdown_requested", signal=str(signum)))
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


def build_message_loop(
    settings: RuntimeSettings,
    credential: Any,
    token_provider: WorkloadTokenProvider,
) -> tuple[MessageLoop, AzureServiceBusAdapter]:
    service_bus = AzureServiceBusAdapter.connect(settings, credential)
    try:
        delivery = CorrelationHttpDelivery(
            settings.correlation_endpoint,
            settings.correlation_timeout_seconds,
            RetryPolicy(
                settings.correlation_max_attempts,
                settings.correlation_backoff_seconds,
                settings.correlation_total_timeout_seconds,
            ),
            token_provider,
            observer=JsonDeliveryObserver(LOGGER),
            allow_loopback_http=settings.environment == "local",
        )
        processor = ProcessEvidence(
            ProcessLog(DrainLogParser(), PatternLogSanitizer()), delivery
        )
        return (
            MessageLoop(
                service_bus,
                service_bus,
                processor,
                JsonLoggingObserver(LOGGER),
            ),
            service_bus,
        )
    except Exception:
        service_bus.close()
        raise


def main(environment: Mapping[str, str] | None = None) -> int:
    try:
        settings = RuntimeSettings.from_environment(
            environment if environment is not None else os.environ
        )
    except InvalidSettings as error:
        configure_logging("ERROR")
        LOGGER.error(_event("configuration_invalid", reason=str(error)))
        return 2

    configure_logging(settings.log_level)
    stop = Event()
    install_shutdown_handlers(stop)

    credential = DefaultAzureCredential() if settings.environment == "azure" else None
    service_bus: AzureServiceBusAdapter | None = None
    server: uvicorn.Server | None = None
    server_thread: Thread | None = None
    health = RuntimeHealth()
    try:
        signing_key = (
            PemSigningKey(settings.signing_key_file)
            if settings.signing_key_file is not None
            else KeyVaultSigningKey(settings.signing_key_id or "", credential)
        )
        token_provider = WorkloadTokenProvider(str(settings.tenant_id), signing_key)
        server = uvicorn.Server(
            uvicorn.Config(
                create_health_app(health, signing_key),
                host=settings.server_address,
                port=settings.server_port,
                access_log=False,
                log_config=None,
            )
        )
        server.install_signal_handlers = lambda: None
        server_thread = Thread(target=server.run, name="nlp-health-api", daemon=True)
        server_thread.start()
        message_loop, service_bus = build_message_loop(settings, credential, token_provider)
        health.receiver(True)
        LOGGER.info(_event("runtime_started"))
        try:
            message_loop.run(stop.is_set)
        except ConsumptionPaused as error:
            health.authorization(False)
            LOGGER.error(_event("consumption_paused", reason=error.code))
            stop.wait()
        LOGGER.info(_event("runtime_stopped"))
        return 0
    except KeyboardInterrupt:
        LOGGER.info(_event("shutdown_requested", signal="keyboard_interrupt"))
        return 0
    except Exception as error:
        LOGGER.error(_event("runtime_failed", error_type=type(error).__name__))
        return 1
    finally:
        try:
            health.receiver(False)
            if service_bus is not None:
                service_bus.close()
            if server is not None:
                server.should_exit = True
            if server_thread is not None:
                server_thread.join(timeout=settings.termination_grace_seconds)
            if credential is not None:
                credential.close()
        except Exception as error:
            LOGGER.error(_event("shutdown_failed", error_type=type(error).__name__))


if __name__ == "__main__":
    raise SystemExit(main())
