# NLP integration contracts — candidate v1

**Status: proposal for review; not approved for implementation or deployment.**

This document narrows PD-033/034/035 for the existing
`azure_service_bus -> nlp_log_processor -> evidence_correlation_engine` flow. It
does not add a relationship, public endpoint, broker entity, permission or cloud
resource. Ingestion, Pipeline, Log/Event and Correlation maintainers must review
the shared fields and failure semantics before this candidate becomes an accepted
contract.

Canonical sources are the current `pipesor-docs` architecture and domain models,
ADR-010, ADR-015, ADR-018 and ADR-019. The source model requires protected
evidence, end-to-end `correlation_id`, provenance, independently arriving data and
logical execution references. It does not guarantee exactly-once delivery or
global ordering.

## 1. Service Bus input

### 1.1 Candidate envelope

Content type is `application/json; charset=utf-8`. Names use snake_case inside
the payload. The broker message ID must equal `message_id`; `correlation_id` must
also be copied to the broker correlation property. W3C `traceparent` and
`tracestate`, when present, travel as broker application properties rather than
business payload fields. `event_type` and `schema_version` are required broker
application properties for subscription filtering and must exactly match the body;
a mismatch is a permanent contract error. Unknown additive fields are ignored within a supported
major version.

```json
{
  "schema_version": "1.0",
  "event_type": "protected_workflow_evidence",
  "message_id": "source-stable-id",
  "occurred_at": "2026-11-07T07:30:00Z",
  "correlation_id": "corr-1042",
  "source": "github_actions",
  "pipeline": {
    "repository": "pipesor-org/example",
    "workflow": "ci"
  },
  "execution": {
    "execution_id": "logical-execution-id",
    "external_run_id": "123456789"
  },
  "evidence": {
    "stage": "BUILD",
    "job": "test",
    "summary": "Build step failed",
    "excerpt": "dependency resolution failed for package example",
    "sequence": 42
  }
}
```

### 1.2 Required and optional fields

| Field | Requirement | Notes |
|---|---|---|
| `schema_version` | Required | Exact `1.x` support policy remains to be accepted. |
| `event_type` | Required | Must be `protected_workflow_evidence` for this candidate. |
| `message_id` | Required | Stable across redelivery; carries idempotency, not authorization. |
| `occurred_at` | Required | RFC 3339 UTC source observation time. |
| `correlation_id` | Required | Opaque text; must contain no sensitive information. |
| `source` | Required | Authorized operational provenance; initial value is `github_actions`. |
| `pipeline.repository` | Required | Canonical owner/repository name; authorization must already be established upstream. |
| `pipeline.workflow` | Required | External workflow identity/name; exact canonical identifier remains cross-service review. |
| `execution.execution_id` | Required | Logical execution reference; ownership/assignment and UUID algorithm remain PD-031/032. |
| `execution.external_run_id` | Required for GitHub Actions | Preserve as text; never reinterpret as internal authorization. |
| `evidence.stage` | Required | Must map to the accepted `PipelineStage` vocabulary. |
| `evidence.job` | Optional | External job when present. |
| `evidence.summary` | Required | Normalized and sanitized upstream. |
| `evidence.excerpt` | Required for NLP processing | Minimized and masked; complete/raw logs are forbidden. |
| `evidence.sequence` | Optional | Source order only; not a broker or global ordering guarantee. |

The payload must not contain credentials, raw complete logs, arbitrary artifacts,
personal data not required for the use case or a replacement Blob locator. NLP
re-sanitizes the excerpt as defense in depth before parsing. A sanitization miss
is a security failure, not a reason to publish the original value in diagnostics.

### 1.3 Compatibility

- Major schema changes require a new `schema_version` major and coordinated
  producer/consumer rollout.
- Additive optional fields are compatible within major version 1.
- Removing, renaming or changing the meaning/type of a required field is breaking.
- Consumers reject unsupported major versions without attempting best-effort
  parsing.
- `message_id`, logical references and enums are compared as canonical values,
  never formatted display strings.

## 2. Delivery, duplicates and ordering

The candidate uses Azure Service Bus peek-lock and assumes **at-least-once**
delivery.

- No global order is assumed. Messages from different executions are independent.
- `sequence` may help correlation inside one source execution but gaps, duplicates
  and late arrivals are valid input conditions.
- `message_id` is propagated to the downstream handoff as the idempotency key.
- Broker duplicate detection may supplement the contract but cannot be the only
  correctness mechanism; its enablement/window is an IaC decision.
- NLP owns no durable table. Durable deduplication cannot be claimed by this
  service unless an approved owner/store is added. Correlation must therefore
  treat repeated `message_id` values idempotently before this flow is integrated.
- A restrictive subscription filter requires removing the default all-pass rule.
  Filters operate on broker application properties, never the JSON body.

### 2.1 Settlement matrix

| Condition | Action | Diagnostic rule |
|---|---|---|
| Valid message and confirmed successful downstream handoff | Complete | Record identifiers and outcome only. |
| Transient broker/network/downstream failure | Abandon | Preserve the broker delivery count; never log payload content. |
| Lock lost before confirmed handoff | Do not complete | Treat a later delivery as possible duplicate. |
| Invalid JSON, unsupported major version or missing required field | Dead-letter | Stable reason code plus field paths; no values. |
| Redactable credential pattern detected after upstream protection | Sanitize and continue | Emit a safe security outcome without the value. |
| Payload cannot be safely minimized | Do not forward | Quarantine/drop and settlement require an approved security policy; DLQ is not automatically safe because it retains the original body. |
| Permanent downstream contract rejection | Dead-letter | Propagate a stable rejection code, not the rejected payload. |
| Repeated transient failure reaches configured maximum delivery count | Broker dead-letter | Maximum count is an environment/IaC value, not selected here. |

Active-message TTL does not expire DLQ content. DLQ inspection, authorized replay
and disposal require an owned operating procedure. Evidence that failed protection
checks is never automatically replayed.

Completion happens only after the synchronous Correlation response confirms that
the handoff succeeded or that the same `message_id` was already accepted. A local
parse followed by a failed handoff is not success.

## 3. Correlation handoff

The existing relationship is internal synchronous REST/HTTPS. The candidate route
is `POST /internal/v1/processed-evidence`; accepting it requires Correlation-owner
review and does not authorize public ingress.

```json
{
  "contract_version": "1.0",
  "message_id": "source-stable-id",
  "correlation_id": "corr-1042",
  "execution_id": "logical-execution-id",
  "source": "github_actions",
  "observed_at": "2026-11-07T07:30:00Z",
  "stage": "BUILD",
  "job": "test",
  "sanitized_content": "dependency resolution failed for package example",
  "template": "dependency resolution failed for package <*>"
}
```

`template` is evidence structure, not a diagnosis. Drain3 cluster IDs are omitted
because the first increment uses process-local state and cannot promise a stable
identifier across replicas or restarts.

The HTTP handoff propagates W3C trace context in standard headers when valid. A
missing or invalid trace header starts a new technical trace linked to the stable
business `correlation_id`; trace identifiers never replace authorization checks.

### 3.1 Candidate responses

| Result | HTTP | Body |
|---|---:|---|
| Accepted and processed | `200` | `{"status":"accepted","message_id":"..."}` |
| Previously accepted idempotency key | `200` | `{"status":"duplicate","message_id":"..."}` |
| Caller payload violates the accepted contract | `400` | Stable code and field paths only. |
| Caller is unauthenticated | `401` | No contract or evidence detail; stop consumption after abandoning the current message. |
| Caller lacks service permission | `403` | No contract or evidence detail; stop consumption after abandoning the current message. |
| Temporary inability to process | `503` | Stable retryable code; optional bounded `Retry-After`. |

Timeouts, connection failures, `429` and `5xx` are transient. Other `4xx` contract
rejections are permanent for that message. NLP must use a bounded timeout and must
not report success without a complete response. A bounded total delivery deadline
must cover token acquisition, HTTP attempts and retry waits; a bounded numeric
`Retry-After` may replace the normal delay only inside that deadline. Exact
timeout/retry budgets remain deployment inputs under PD-035. `401` and `403` are
service configuration/authorization failures, not evidence failures: the current
message is abandoned and consumption stops to prevent valid evidence entering DLQ.

Correlation must durably enforce `message_id` idempotency, including when its
successful response is lost. Reusing one ID with different content is a conflict,
not `duplicate`. The required incident/evidence references and temporarily missing
reference behavior remain a cross-service blocker; NLP must not read another
service's database to fill that gap.

## 4. Authentication proposal

- Service Bus consumption uses the Container App system-assigned managed identity
  with the minimum data-plane receive permission scoped to the approved
  subscription. It receives no broker-management permission.
- NLP-to-Correlation uses Entra workload authentication from the NLP managed
  identity to a Correlation-specific audience and application role.
- No shared API key, connection string or client secret is introduced by this
  proposal.
- Exact Azure role assignment, audience, application role, token validation,
  tenant and network rules require backend/IaC approval under PD-027/034/035.

Authentication establishes the caller identity; it does not authorize evidence
from every pipeline. The receiving service must enforce the agreed resource scope.

## 5. Drain3 state lifecycle

For the first controlled MVP candidate:

- State is in memory and isolated per process.
- State is not restored after restart and is not shared across replicas.
- Cluster IDs are operationally unstable and never leave the service contract.
- The template text is the only Drain3-derived downstream value.
- Unrelated execution context is never merged merely because templates match;
  context remains attached to each processed message.
- Parser tuning is versioned with the application and tested using synthetic,
  sanitized fixtures.

This choice avoids inventing a database, workload Blob or unapproved cache for a
service with no owned tables. If stable clusters or multi-replica learning become
required, a separate reviewed decision must assign state ownership, isolation key,
storage, retention, concurrency and migration behavior before implementation.

## 6. Alignment with the IaC foundation

This proposal was checked against `pipesor-iac` commit
`4b42c0c8e66578dfb890ae1cca8c9c31c774026e`. That revision provides the following
boundaries, but does not make this application contract deployable by itself:

- The optional Service Bus foundation is a public, Entra-only Standard namespace
  with TLS 1.2 and local authentication disabled. It creates no topic,
  subscription, duplicate-detection window, maximum-delivery policy or runtime
  role assignment, so those remain explicit contract and deployment decisions.
- The Container Apps foundation is an environment boundary, not an NLP
  application deployment. A system-assigned identity per future app is approved,
  while the app, its audience and its scoped Service Bus/Correlation grants remain
  pending.
- The optional Microsoft Foundry foundation exposes an Entra-only account/project
  interface and service-reported endpoint. It deliberately creates no model
  deployment or runtime grant; this NLP contract therefore claims no Azure OpenAI
  capability.
- IaC records these as locally prepared foundations. This review performed no
  Azure write, deployment, state mutation or access grant.

If the IaC revision or module interfaces change, this alignment must be reviewed
again before implementation or deployment.

## 7. Decisions required for acceptance

1. Ingestion, Pipeline and Log/Event owners confirm the shared envelope and the
   owner/format of `execution_id`, repository/workflow identity and stage values.
2. Correlation owner accepts the route, request/response shapes and idempotent
   handling of `message_id`.
3. Backend/IaC owners accept peek-lock settlement, dead-letter reason taxonomy,
   max-delivery ownership and managed-identity boundaries.
4. Security review confirms upstream protection plus NLP defense-in-depth and the
   prohibition on payload values in diagnostics, including the quarantine/drop
   policy for a payload that cannot be safely minimized.
5. Product/architecture authority accepts process-local Drain3 state and unstable
   cluster IDs for the controlled MVP.
6. Accepted decisions are reflected in the owning cross-repository model/ADR or
   pending-decision register before live integration is claimed.

