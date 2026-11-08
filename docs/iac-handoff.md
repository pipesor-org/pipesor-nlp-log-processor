# NLP deployment handoff to IaC

**Status: candidate for backend/IaC acceptance; no Azure action authorized.**

This record reconciles `pipesor-nlp-log-processor@99874e56455d23b050fac91c5895f1c3c6b5a951`
with `pipesor-iac@4b42c0c8e66578dfb890ae1cca8c9c31c774026e` for the first
`dev` deployment slice. It defines the interface between repositories; it neither
selects missing environment values nor proves that any resource exists in Azure.

## Evidence available from NLP

- The worker consumes a Service Bus topic subscription in peek-lock mode through
  `DefaultAzureCredential`, renews locks for a bounded configured duration and
  explicitly completes, abandons or dead-letters the original message.
- It sends only the sanitized candidate contract to Correlation over HTTPS using
  an Entra bearer-token scope supplied at runtime. Timeouts and retries are bounded
  configuration inputs.
- `SIGINT`/`SIGTERM` request cooperative shutdown between receives. The worker has
  no listening port or inbound HTTP route.
- The digest-pinned Python 3.14.8 image runs as UID/GID `10001`. GitHub Actions
  executes 44 tests, builds the image and verifies its safe configuration failure.
- No image has been published to GHCR and no live Azure integration has run.

NLP currently ends after protected evidence sanitization, Drain3 structuring and
Correlation delivery. Moving embedding ownership to RAG is a candidate architecture
decision, not an accepted fact: the canonical model still names an NLP embedding
capability. No NLP OpenAI SDK, model, dimension, endpoint or permission may be
introduced until that model decision is recorded.

## Current IaC boundary

The referenced IaC revision can optionally create a public, Entra-only Service Bus
Standard namespace and a shared Container Apps managed environment. It exports the
namespace name and managed-environment ID. It does **not** create:

- a Service Bus topic, subscription, filters or delivery configuration;
- an NLP Container App or its system-assigned identity;
- Service Bus Data Receiver role assignments;
- a Correlation app registration, audience, application role or role assignment;
- a GHCR package, immutable image digest or registry pull authorization.

## Runtime configuration handoff

| NLP variable | Required supplier / derivation | Current status |
|---|---|---|
| `PIPESOR_SERVICE_BUS_NAMESPACE` | IaC namespace name rendered as `<name>.servicebus.windows.net`. | Namespace interface exists; actual `dev` value/provisioning unverified. |
| `PIPESOR_SERVICE_BUS_TOPIC` | IaC-owned approved topic name. | Blocked: no approved entity name/resource. |
| `PIPESOR_SERVICE_BUS_SUBSCRIPTION` | IaC-owned NLP subscription name. | Blocked: no approved name/resource/filter. |
| `PIPESOR_SERVICE_BUS_RECEIVE_WAIT_SECONDS` | Backend/IaC accepted receive/shutdown budget. | Required by worker; value not accepted. |
| `PIPESOR_SERVICE_BUS_MAX_LOCK_RENEWAL_SECONDS` | Backend/IaC accepted processing/lock budget. | Required by worker; value not accepted. |
| `PIPESOR_CORRELATION_ENDPOINT` | Correlation deployment output; internal HTTPS base URL. | Blocked: no Correlation app/route deployment contract. |
| `PIPESOR_CORRELATION_SCOPE` | Correlation identity owner; one Entra `/.default` scope. | Blocked: audience/application role not accepted. |
| `PIPESOR_CORRELATION_TIMEOUT_SECONDS` | Backend/IaC accepted request budget. | Required by worker; value not accepted. |
| `PIPESOR_CORRELATION_TOTAL_TIMEOUT_SECONDS` | Backend/IaC accepted end-to-end authentication, HTTP and wait deadline. | Required by worker; value not accepted. |
| `PIPESOR_CORRELATION_MAX_ATTEMPTS` | Backend/IaC accepted bounded retry policy. | Required by worker; value not accepted. |
| `PIPESOR_CORRELATION_BACKOFF_SECONDS` | Backend/IaC accepted retry delay. | Required by worker; value not accepted. |
| `PIPESOR_LOG_LEVEL` | Deployment setting; defaults to `INFO`. | Interface implemented; environment value optional. |

These are nonsecret settings. The worker accepts no Service Bus connection string,
client secret or static Correlation API key. Managed identity supplies runtime
credentials; no secret store is requested by this slice.

## Required infrastructure slice

After the blocked values and contracts are accepted, the smallest IaC increment is:

1. Add the approved topic and NLP subscription to the existing optional namespace,
   including accepted TTL, maximum delivery count, expiry dead-lettering and filter.
2. Consume an immutable GHCR image digest tied to an NLP source revision.
3. Create one `dev` Container App with no ingress and a system-assigned identity.
4. Supply the nonsecret variables above as Container App environment values.
5. Grant that identity Azure Service Bus Data Receiver scoped to the NLP
   subscription, not namespace management or sender access.
6. Grant the same identity only the accepted Correlation application role/audience.
7. Route console output through the existing Container Apps/Log Analytics platform.

The reviewed initial `dev` proposal is topic `protected-workflow-evidence`, NLP
subscription `nlp-log-processor`, a restrictive
`event_type = 'protected_workflow_evidence'` filter, seven-day active-message TTL,
maximum delivery count 10, 60-second initial lock, 300-second renewal ceiling and
five-second receive wait. This remains conditional on Ingestion, Pipeline and
Log/Event accepting the shared topic and application-property contract. IaC must
remove the subscription's default all-pass rule when installing the restrictive
filter; otherwise the filter provides no isolation.

The active-message TTL does not expire DLQ contents. Before activation, maintainers
must own a bounded inspection, authorized replay and disposal procedure. Messages
that failed evidence-protection checks must never be replayed automatically or
exposed through diagnostic tooling.

Outside the temporary validation deployment below, production CPU, memory, replica
bounds, Service Bus scale rules, termination grace and probe behavior remain
unselected. Because there is no inbound endpoint, IaC must not infer a public
ingress, port or HTTP probe. Scale-to-zero must not be selected without an accepted
Service Bus scaler identity/permission and shutdown contract.

For the temporary validation deployment, the reviewed container proposal is no
ingress, no public port, 0.5 CPU, 1 GiB memory, one fixed replica, no broker scaler
and a digest-pinned image. The validated shutdown budget is a five-second receive
wait plus a 40-second total delivery deadline within a 60-second termination grace,
leaving at least ten seconds for settlement and cleanup. The worker exits on
irrecoverable authorization/configuration failure so ACA can restart it. No HTTP,
TCP or executable probe is accepted for this first worker increment; progress must
be monitored from safe activity, outcome, latency and retry telemetry.

## Acceptance and activation gates

Before Terraform implementation, backend and IaC owners must record acceptance of:

- entity names, filter/eligibility rules, TTL, delivery count and DLQ ownership;
- receive, lock, HTTP timeout and retry budgets;
- Correlation URL, Entra audience/application role and authorization enforcement;
- immutable GHCR image path/digest plus publication and pull responsibility;
- Container App CPU, memory, replicas, scaling and termination behavior.

After implementation, local Terraform tests and a reviewed `dev` plan are still
required. Contract acceptance or PR merge does not authorize `terraform apply`,
state changes, role grants, image publication or any other Azure write.
