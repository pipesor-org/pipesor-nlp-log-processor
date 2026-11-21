"""Opt-in NLP runtime on the local protected evidence mailbox."""
import argparse
from pathlib import Path
from threading import Event

from .application.message_loop import ConsumptionPaused, MessageLoop
from .application.process_evidence import ProcessEvidence
from .application.process_log import ProcessLog
from .infrastructure.correlation_http import CorrelationHttpDelivery, RetryPolicy
from .infrastructure.drain_parser import DrainLogParser
from .infrastructure.local_spool import LocalBearer, LocalSpool, LoopbackCorrelationTransport
from .infrastructure.observability import JsonLoggingObserver
from .infrastructure.sanitizer import PatternLogSanitizer
from .main import LOGGER, configure_logging, install_shutdown_handlers


def main() -> int:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--outbox", type=Path, required=True)
    args.add_argument("--correlation-port", type=int, required=True, choices=range(1, 65536), metavar="PORT")
    args.add_argument("--token-file", type=Path, required=True)
    options = args.parse_args()
    configure_logging("INFO")
    stop = Event()
    install_shutdown_handlers(stop)
    spool = LocalSpool(options.outbox)
    delivery = CorrelationHttpDelivery(
        "https://pipesor.local", 10, RetryPolicy(1, 0, 15),
        LocalBearer(options.token_file), LoopbackCorrelationTransport(options.correlation_port),
    )
    loop = MessageLoop(spool, spool, ProcessEvidence(ProcessLog(DrainLogParser(), PatternLogSanitizer()), delivery),
                       JsonLoggingObserver(LOGGER))
    try:
        loop.run(stop.is_set)
        return 0
    except ConsumptionPaused:
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
