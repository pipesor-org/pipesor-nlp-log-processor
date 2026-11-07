from typing import Protocol

from ..domain.models import ParsedTemplate


class LogParser(Protocol):
    def parse(self, sanitized_content: str) -> ParsedTemplate: ...


class LogSanitizer(Protocol):
    def sanitize(self, content: str) -> str: ...

