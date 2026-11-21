"""Local mailbox transport for protected workflow evidence."""
import base64
import json
import os
import re
import stat
import time
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException
from pathlib import Path

from ..application.integration_ports import ReceivedMessage
from .correlation_http import HttpResponse, TransportFailure

_LIMIT = 512 * 1024
_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


def read_private(path: Path, limit: int) -> bytes:
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("Local transport requires an operator-owned 0600 file")
        body = file.read(limit + 1)
    if len(body) > limit:
        raise ValueError("Local transport limit exceeded")
    return body


class LocalSpool:
    def __init__(self, directory: Path) -> None:
        info = directory.lstat()
        if not directory.is_absolute() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("The local mailbox must be operator owned with mode 0700")
        self.directory = directory
        self._retry = {}
        self._snapshots = set()

    def receive(self) -> ReceivedMessage | None:
        for path in sorted(self.directory.glob("*.json")):
            if not _ID.fullmatch(path.stem) or path.stem in self._snapshots:
                continue
            if self._retry.get(path.stem, 0) > time.monotonic() or self._marker(path.stem, "done").exists() or self._marker(path.stem, "rejected").exists():
                continue
            document = json.loads(read_private(path, _LIMIT))
            props = document["applicationProperties"]
            if props["eventType"] == "execution.snapshot":
                self._snapshots.add(path.stem)
                continue
            if document["messageId"] != path.stem:
                raise ValueError("Local publication message ID mismatch")
            return ReceivedMessage(
                body=base64.b64decode(document["bodyBase64"], validate=True),
                message_id=path.stem,
                correlation_id=document["correlationId"],
                broker_event_type=props["eventType"],
                broker_schema_major=props["schemaMajor"],
            )
        time.sleep(0.5)
        return None

    def _marker(self, message_id: str, outcome: str) -> Path:
        return self.directory / f".nlp-{message_id}.{outcome}"

    def _settle(self, message: ReceivedMessage, outcome: str) -> None:
        # A crash before settlement redelivers. Correlation still owns idempotency.
        try:
            fd = os.open(self._marker(message.message_id, outcome), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            folder = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(folder)
            finally:
                os.close(folder)
        except FileExistsError:
            pass
        self._retry.pop(message.message_id, None)

    def complete(self, message: ReceivedMessage) -> None:
        self._settle(message, "done")

    def abandon(self, message: ReceivedMessage) -> None:
        self._retry[message.message_id] = time.monotonic() + 5

    def dead_letter(self, message: ReceivedMessage, reason: str, field_paths: tuple[str, ...]) -> None:
        self._settle(message, "rejected")


@dataclass(frozen=True)
class LocalBearer:
    file: Path

    def get_token(self) -> str:
        token = read_private(self.file, 8192).decode("ascii").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token):
            raise ValueError("A signed local NLP workload token is required")
        return token


@dataclass(frozen=True)
class LoopbackCorrelationTransport:
    port: int

    def post(self, url, body, headers, timeout_seconds) -> HttpResponse:
        if url != "https://pipesor.local/internal/v1/processed-evidence":
            raise TransportFailure("invalid_local_correlation_target")
        client = HTTPConnection("127.0.0.1", self.port, timeout=timeout_seconds)
        try:
            client.request("POST", "/internal/v1/processed-evidence", body=body, headers=dict(headers))
            reply = client.getresponse()
            payload = reply.read(16385)
            if len(payload) > 16384:
                raise TransportFailure("local_correlation_reply_too_large")
            return HttpResponse(reply.status, payload, dict(reply.getheaders()))
        except (HTTPException, OSError) as error:
            raise TransportFailure("local_correlation_unavailable") from error
        finally:
            client.close()
