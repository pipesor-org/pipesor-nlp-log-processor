from .ports import LogParser, LogSanitizer
from ..domain.models import LogEnvelope, ProcessedLog


class ProcessLog:
    def __init__(
        self,
        parser: LogParser,
        sanitizer: LogSanitizer,
    ) -> None:
        self._parser = parser
        self._sanitizer = sanitizer

    def execute(self, envelope: LogEnvelope) -> ProcessedLog:
        sanitized_content = self._sanitizer.sanitize(envelope.content)
        parsed = self._parser.parse(sanitized_content)
        return ProcessedLog(
            sanitized_content=sanitized_content,
            correlation_id=envelope.correlation_id,
            execution_id=envelope.execution_id,
            source=envelope.source,
            cluster_id=parsed.cluster_id,
            template=parsed.template,
        )

