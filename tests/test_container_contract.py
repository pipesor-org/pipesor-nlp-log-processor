from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_container_uses_pinned_python_and_non_root_runtime() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert dockerfile.count("python:3.14.8-slim@sha256:") == 2
    assert "USER 10001:10001" in dockerfile
    assert 'ENTRYPOINT ["pipesor-nlp-log-processor"]' in dockerfile
    assert "HEALTHCHECK" not in dockerfile
    assert "COPY ." not in dockerfile


def test_container_example_lists_every_runtime_variable() -> None:
    keys = {
        line.split("=", 1)[0]
        for line in (ROOT / "container.env.example").read_text().splitlines()
        if line and not line.startswith("#")
    }

    assert keys == {
        "PIPESOR_SERVICE_BUS_NAMESPACE",
        "PIPESOR_SERVICE_BUS_TOPIC",
        "PIPESOR_SERVICE_BUS_SUBSCRIPTION",
        "PIPESOR_SERVICE_BUS_RECEIVE_WAIT_SECONDS",
        "PIPESOR_SERVICE_BUS_MAX_LOCK_RENEWAL_SECONDS",
        "PIPESOR_CORRELATION_ENDPOINT",
        "PIPESOR_CORRELATION_SCOPE",
        "PIPESOR_CORRELATION_TIMEOUT_SECONDS",
        "PIPESOR_CORRELATION_TOTAL_TIMEOUT_SECONDS",
        "PIPESOR_CORRELATION_MAX_ATTEMPTS",
        "PIPESOR_CORRELATION_BACKOFF_SECONDS",
        "PIPESOR_LOG_LEVEL",
    }
