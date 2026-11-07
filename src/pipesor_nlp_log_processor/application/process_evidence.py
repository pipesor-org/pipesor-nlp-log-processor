from .integration_ports import DeliveryStatus, EvidenceDelivery, ReceivedMessage
from .process_log import ProcessLog
from ..domain.integration import CorrelationEvidence, InvalidIntegrationMessage, ProtectedEvidenceMessage
from ..domain.models import LogEnvelope


class ProcessEvidence:
    def __init__(self, processor: ProcessLog, delivery: EvidenceDelivery) -> None:
        self._processor = processor
        self._delivery = delivery

    def execute(self, received: ReceivedMessage) -> DeliveryStatus:
        if received.contract_error is not None:
            raise InvalidIntegrationMessage(received.contract_error)
        message = ProtectedEvidenceMessage.from_json(received.body)
        mismatches = tuple(
            path
            for path, broker_value, body_value in (
                ("message_id", received.message_id, message.message_id),
                ("correlation_id", received.correlation_id, message.correlation_id),
            )
            if broker_value != body_value
        )
        if mismatches:
            raise InvalidIntegrationMessage("broker_property_mismatch", mismatches)

        processed = self._processor.execute(
            LogEnvelope(
                content=message.excerpt,
                correlation_id=message.correlation_id,
                execution_id=message.execution_id,
                source=message.source,
            )
        )
        return self._delivery.deliver(
            CorrelationEvidence(
                contract_version="1.0",
                message_id=message.message_id,
                correlation_id=message.correlation_id,
                execution_id=message.execution_id,
                source=message.source,
                observed_at=message.occurred_at,
                stage=message.stage,
                job=message.job,
                sanitized_content=processed.sanitized_content,
                template=processed.template,
                traceparent=received.traceparent,
                tracestate=received.tracestate,
            )
        )

