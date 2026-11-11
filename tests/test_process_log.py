import pytest

from pipesor_nlp_log_processor import (
    DrainLogParser,
    LogEnvelope,
    PatternLogSanitizer,
    ProcessLog,
)
from pipesor_nlp_log_processor.application.ports import LogParser
from pipesor_nlp_log_processor.domain.models import InvalidLogEnvelope, ParsedTemplate
from pipesor_nlp_log_processor.infrastructure.drain_parser import LogParsingError


def envelope(content: str) -> LogEnvelope:
    return LogEnvelope(
        content=content,
        correlation_id="corr-1042",
        execution_id="run-88",
        source="github-actions",
    )


def processor(parser: LogParser | None = None) -> ProcessLog:
    return ProcessLog(parser or DrainLogParser(), PatternLogSanitizer())


def test_rejects_empty_log_content() -> None:
    with pytest.raises(InvalidLogEnvelope, match="content"):
        envelope("   ")


def test_sanitizes_secrets_before_parsing_and_preserves_context() -> None:
    result = processor().execute(
        envelope("deployment failed token=super-secret-value status=403")
    )

    assert "super-secret-value" not in result.sanitized_content
    assert "[REDACTED]" in result.sanitized_content
    assert result.correlation_id == "corr-1042"
    assert result.execution_id == "run-88"
    assert result.source == "github-actions"


@pytest.mark.parametrize(
    ("content", "secret"),
    [
        ('{"password": "synthetic secret with spaces"}', "synthetic secret"),
        ('password="quoted secret with spaces"', "quoted secret"),
        ("client_secret='single quoted secret'", "single quoted secret"),
        ("token=unquoted-secret", "unquoted-secret"),
    ],
)
def test_sanitizes_common_assignment_formats(content: str, secret: str) -> None:
    sanitized = processor().sanitize(content)

    assert secret not in sanitized
    assert "[REDACTED]" in sanitized


def test_groups_equivalent_sanitized_logs_without_merging_context() -> None:
    log_processor = processor()

    first = log_processor.execute(envelope("job build-120 failed with status 500"))
    second = log_processor.execute(
        LogEnvelope(
            content="job build-121 failed with status 503",
            correlation_id="corr-other",
            execution_id="run-other",
            source="github-actions",
        )
    )

    assert first.cluster_id == second.cluster_id
    assert first.correlation_id == "corr-1042"
    assert second.correlation_id == "corr-other"
    assert first.execution_id != second.execution_id


class FailingParser:
    def parse(self, sanitized_content: str) -> ParsedTemplate:
        raise RuntimeError(sanitized_content)


def test_parser_failures_do_not_expose_sensitive_input() -> None:
    secret = "do-not-leak-this-secret"

    with pytest.raises(RuntimeError) as caught:
        processor(FailingParser()).execute(envelope(f"token={secret}"))

    assert secret not in str(caught.value)


def test_drain_adapter_uses_safe_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    parser = DrainLogParser()

    def fail(_: str) -> None:
        raise RuntimeError("raw-sensitive-log")

    monkeypatch.setattr(parser._miner, "add_log_message", fail)
    with pytest.raises(LogParsingError, match="Unable to parse sanitized log") as caught:
        parser.parse("raw-sensitive-log")
    assert "raw-sensitive-log" not in str(caught.value)

