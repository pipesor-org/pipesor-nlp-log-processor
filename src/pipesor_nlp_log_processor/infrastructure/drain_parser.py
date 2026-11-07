from threading import Lock

from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig

from ..domain.models import ParsedTemplate


class LogParsingError(RuntimeError):
    """Safe parsing failure that never includes log content."""


class DrainLogParser:
    """In-memory Drain3 adapter; parser-state persistence remains undecided."""

    def __init__(self) -> None:
        config = TemplateMinerConfig()
        config.profiling_enabled = False
        self._miner = TemplateMiner(config=config)
        self._lock = Lock()

    def parse(self, sanitized_content: str) -> ParsedTemplate:
        try:
            with self._lock:
                result = self._miner.add_log_message(sanitized_content)
            return ParsedTemplate(
                cluster_id=int(result["cluster_id"]),
                template=str(result["template_mined"]),
            )
        except Exception:
            raise LogParsingError("Unable to parse sanitized log") from None

