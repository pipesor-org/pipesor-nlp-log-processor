import json
import logging
import os
import signal
from collections.abc import Mapping
from threading import Event
from typing import Any

from azure.identity import DefaultAzureCredential

from .application.message_loop import ConsumptionPaused, MessageLoop
from .application.process_evidence import ProcessEvidence
from .application.process_log import ProcessLog
from .infrastructure.correlation_http import (
    AzureCredentialTokenProvider,
    CorrelationHttpDelivery,
    RetryPolicy,
)
from .infrastructure.drain_parser import DrainLogParser
from .infrastructure.observability import JsonLoggingObserver
from .infrastructure.sanitizer import PatternLogSanitizer
from .infrastructure.service_bus import AzureServiceBusAdapter
from .infrastructure.settings import InvalidSettings, RuntimeSettings


LOGGER = logging.getLogger("pipesor_nlp_log_processor")


def _event(name: str, **fields: str) -> str:
    return json.dumps({"event": name, **fields}, separators=(",", ":"))


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level), format="%(message)s", force=True)


def build_message_loop(
    settings: RuntimeSettings,
    credential: Any,
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
            AzureCredentialTokenProvider(credential, settings.correlation_scope),
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

    def request_stop(signum: int, _: object) -> None:
        LOGGER.info(_event("shutdown_requested", signal=str(signum)))
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    credential = DefaultAzureCredential()
    service_bus: AzureServiceBusAdapter | None = None
    try:
        message_loop, service_bus = build_message_loop(settings, credential)
        LOGGER.info(_event("runtime_started"))
        message_loop.run(stop.is_set)
        LOGGER.info(_event("runtime_stopped"))
        return 0
    except KeyboardInterrupt:
        LOGGER.info(_event("shutdown_requested", signal="keyboard_interrupt"))
        return 0
    except ConsumptionPaused as error:
        LOGGER.error(_event("consumption_paused", reason=error.code))
        return 3
    except Exception as error:
        LOGGER.error(_event("runtime_failed", error_type=type(error).__name__))
        return 1
    finally:
        try:
            if service_bus is not None:
                service_bus.close()
            credential.close()
        except Exception as error:
            LOGGER.error(_event("shutdown_failed", error_type=type(error).__name__))


if __name__ == "__main__":
    raise SystemExit(main())
