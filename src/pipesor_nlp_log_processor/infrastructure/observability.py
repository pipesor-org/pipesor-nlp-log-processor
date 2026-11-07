import json
import logging


class JsonLoggingObserver:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def record(
        self, outcome: str, message_id: str, correlation_id: str, reason: str | None
    ) -> None:
        event = {
            "event": "nlp_message_processed",
            "outcome": str(outcome),
            "message_id": message_id,
            "correlation_id": correlation_id,
        }
        if reason is not None:
            event["reason"] = reason
        self._logger.info(json.dumps(event, separators=(",", ":")))

