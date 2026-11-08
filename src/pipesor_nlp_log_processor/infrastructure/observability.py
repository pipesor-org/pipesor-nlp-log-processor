import json
import logging


class JsonLoggingObserver:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def record(
        self,
        outcome: str,
        message_id: str,
        correlation_id: str,
        reason: str | None,
        duration_seconds: float,
    ) -> None:
        event = {
            "event": "nlp_message_processed",
            "outcome": str(outcome),
            "message_id": message_id,
            "correlation_id": correlation_id,
            "duration_ms": round(duration_seconds * 1000, 3),
        }
        if reason is not None:
            event["reason"] = reason
        self._logger.info(json.dumps(event, separators=(",", ":")))
        self._logger.info(
            json.dumps(
                {
                    "event": "nlp_metric",
                    "metric": "message_processing_duration_ms",
                    "value": round(duration_seconds * 1000, 3),
                    "outcome": str(outcome),
                },
                separators=(",", ":"),
            )
        )


class JsonDeliveryObserver:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def record_retry(self, reason: str, attempt: int, delay_seconds: float) -> None:
        self._logger.info(
            json.dumps(
                {
                    "event": "nlp_metric",
                    "metric": "correlation_retry",
                    "value": 1,
                    "reason": reason,
                    "attempt": attempt,
                    "delay_ms": round(delay_seconds * 1000, 3),
                },
                separators=(",", ":"),
            )
        )

