from .integration_ports import DeliveryStatus, EvidenceDelivery, ReceivedMessage
from .process_log import ProcessLog
from ..domain.integration import (
    EVENT_TYPE,
    SCHEMA_MAJOR,
    CorrelationEvidence,
    InvalidIntegrationMessage,
    ProtectedEvidenceMessage,
)
from ..domain.models import LogEnvelope


class ProcessEvidence:
    def __init__(self, processor: ProcessLog, delivery: EvidenceDelivery) -> None:
        self._processor = processor
        self._delivery = delivery

    def execute(self, received: ReceivedMessage) -> DeliveryStatus:
        if received.contract_error is not None:
            raise InvalidIntegrationMessage(received.contract_error)
        message = ProtectedEvidenceMessage.from_json(received.body)
        property_mismatches = tuple(
            path
            for path, broker_value, body_value in (
                ("eventType", received.broker_event_type, EVENT_TYPE),
                ("schemaMajor", received.broker_schema_major, SCHEMA_MAJOR),
            )
            if broker_value != body_value
        )
        if property_mismatches:
            raise InvalidIntegrationMessage(
                "broker_property_mismatch", property_mismatches
            )
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
                content=message.evidence.excerpt,
                correlation_id=message.correlation_id,
                execution_id=message.execution_id,
                source=message.source,
            )
        )
        return self._delivery.deliver(
            CorrelationEvidence(
                contract_version=message.schema_version,
                source_locator=message.evidence.source_locator,
                message_id=message.message_id,
                correlation_id=message.correlation_id,
                source=message.source,
                occurred_at=message.occurred_at,
                source_updated_at=message.source_updated_at,
                capture_context=message.capture_context,
                pipeline=message.pipeline,
                execution=message.execution,
                commit=message.commit,
                service=message.service,
                environment=message.environment,
                pipeline_id=message.pipeline_id,
                execution_id=message.execution_id,
                incident_id=message.incident_id,
                evidence_id=message.evidence_id,
                stage=message.evidence.stage,
                job=message.evidence.job,
                sequence=message.evidence.sequence,
                sanitized_summary=self._processor.sanitize(message.evidence.summary),
                sanitized_content=processed.sanitized_content,
                template=processed.template,
                traceparent=received.traceparent,
                tracestate=received.tracestate,
            )
        )

