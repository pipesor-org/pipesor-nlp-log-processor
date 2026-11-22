# pipesor-nlp-log-processor

NLP Log Processor preprocesses protected logs, structures them with Drain3, prepares semantic signals and delivers processed evidence to the correlation service. Its canonical architecture ID is `nlp_log_processor`; the planned runtime is Python / FastAPI on Azure Container Apps.

The processing worker is implemented as a Python package. It validates the locally approved Ingestion evidence v2 envelope and broker identifiers, redacts common credential shapes before parsing, structures sanitized logs with an in-memory Drain3 adapter and maps the result to the Correlation v2 candidate. Typed ports keep Azure Service Bus and HTTP SDKs outside the domain. The message loop completes only after a confirmed handoff, abandons transient or unexpected failures, dead-letters safe permanent contract failures and supports cooperative shutdown between receives. The repository includes live Service Bus and Correlation adapters but no deployed resources, approved production identifiers, Azure OpenAI call or inbound business HTTP API.

The Correlation adapter sends exact platform workload JWTs, permits local loopback
HTTP only in `local`, and otherwise requires HTTPS. Service Bus uses the emulator
connection string locally and namespace plus `DefaultAzureCredential` in Azure.
Protected evidence 2.0, 2.1 and generalized 2.2 locators are accepted. Drain3 state
remains process-local, so the Container App must run exactly one replica.

## Documentation

- [Documentation baseline](docs/documentation-baseline.md): audited source revisions, evidence limits and adoption after merge.

- [Architecture](docs/architecture.md): service boundaries, integration context and current sources in `pipesor-docs`.
- [Deployment contract](docs/deployment-contract.md): development baseline, delivery requirements and unresolved contracts.
- [Integration contracts](docs/integration-contracts.md): adopted Ingestion evidence v2 input, Correlation v2 candidate, delivery semantics, authentication and Drain3 lifecycle.
- [IaC handoff](docs/iac-handoff.md): exact worker inputs, current infrastructure gaps and activation gates for the first `dev` deployment slice.
- [Image assurance](docs/image-assurance.md): SBOM, vulnerability policy, digest evidence, termination validation and publication boundary.
- [AGENTS.md](AGENTS.md): repository instructions for coding agents.

## Development and verification

The [development baseline](docs/deployment-contract.md#development-baseline) selects Python 3.14.8. Create an isolated environment and install the pinned application and development dependencies:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Run the verified checks:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m compileall -q src tests tools
git diff --check
```

When Docker Desktop is running, also build the image and verify the safe
configuration failure using the commands in the [Container](#container) section.

After supplying the reviewed runtime variables, start the worker with:

```powershell
.\.venv\Scripts\pipesor-nlp-log-processor.exe
```

Required variables are `PIPESOR_ENVIRONMENT`, `PIPESOR_TENANT_ID`,
`PIPESOR_WORKLOAD_ID=nlp`, `SERVER_ADDRESS`, `SERVER_PORT`,
`PIPESOR_SERVICE_BUS_CONNECTION_STRING` (local) or
`PIPESOR_SERVICE_BUS_NAMESPACE` (Azure),
`PIPESOR_SERVICE_BUS_TOPIC=workflow-evidence`, `PIPESOR_SERVICE_BUS_SUBSCRIPTION=nlp`,
`PIPESOR_SERVICE_BUS_RECEIVE_WAIT_SECONDS`,
`PIPESOR_SERVICE_BUS_MAX_LOCK_RENEWAL_SECONDS`,
`PIPESOR_ENDPOINTS_CORRELATION`, and `PIPESOR_SIGNING_KEY_FILE` (local) or
`PIPESOR_SIGNING_KEY_ID` (Azure), plus
`PIPESOR_CORRELATION_TIMEOUT_SECONDS`,
`PIPESOR_CORRELATION_TOTAL_TIMEOUT_SECONDS`,
`PIPESOR_CORRELATION_MAX_ATTEMPTS` and
`PIPESOR_CORRELATION_BACKOFF_SECONDS`, plus
`PIPESOR_TERMINATION_GRACE_SECONDS`. `PIPESOR_LOG_LEVEL` is optional and
defaults to `INFO`. Values remain reviewed deployment inputs; this repository
does not provide production identifiers or credentials.

The worker validates all settings before creating clients, consumes Service Bus,
delivers sanitized evidence to Correlation and handles `SIGINT`/`SIGTERM` between
receives. Its internal HTTP server exposes `/health/live`, `/health/ready`, and
`/.well-known/jwks.json`; it has no inbound business endpoint.

Structured stdout telemetry distinguishes accepted and duplicate completion,
abandonment, dead-letter and authorization pause outcomes. Processing-duration and
Correlation-retry metric events use only bounded outcome/reason fields; message and
correlation IDs remain log fields and are not metric dimensions. The validation
deployment uses a five-second receive wait, 40-second total delivery deadline and
60-second termination grace budget.

## Container

The multi-stage image uses the digest-pinned official Python 3.14.8 slim base,
installs only the application wheel in the runtime stage and runs as UID/GID
`10001`. Build it with its source revision recorded in the OCI metadata:

```powershell
$revision = git rev-parse HEAD
docker build --build-arg "VCS_REF=$revision" --tag pipesor-nlp-log-processor:local .
```

Running the image without configuration is a safe startup validation: it exits
with code `2` before creating Azure clients. For an authorized local integration,
copy `container.env.example` outside version control, supply the reviewed nonsecret
identifiers and run:

```powershell
docker run --rm --env-file .env.local pipesor-nlp-log-processor:local
```

Do not commit tokens, connection strings or signing keys. Correlation discovers the
public signing key through JWKS. Readiness fails while the Service Bus receiver is
down or after Correlation returns 401/403. Configure ACA HTTP probes on port 8080;
do not expose public ingress.

CI runs the same Python checks and validates PR titles, required PR-description sections and commit headers against the organization Conventional Commit policy. Human review is still required for English imperative summaries, reported verification and behavioral correctness.

For the current documentation foundation, check affected links and consistency with the referenced sources, then run:

```bash
git diff --check
```

For TOML configuration changes, run this syntax check with Python 3.11+ (the selected Python baseline supports `tomllib`):

```bash
python3 -c 'from pathlib import Path; import tomllib; [tomllib.loads(p.read_text()) for p in Path(".codex").rglob("*.toml")]; print("TOML syntax OK")'
```

Follow the organization [contribution policy](https://github.com/pipesor-org/.github/blob/main/CONTRIBUTING.md), [code of conduct](https://github.com/pipesor-org/.github/blob/main/CODE_OF_CONDUCT.md) and [security policy](https://github.com/pipesor-org/.github/blob/main/SECURITY.md). Licensing remains pending.

## Optional Codex setup

Start Codex from this repository root so its [repository instructions](AGENTS.md) are discovered. Project configuration is in [.codex/config.toml](.codex/config.toml); [nlp_reviewer](.codex/agents/nlp-reviewer.toml) defines a focused reviewer. Review roles do not create separate sandbox permissions.

After changing instructions, start a fresh session and ask it to identify its active instruction sources. See the official [instruction-discovery guide](https://learn.chatgpt.com/docs/agent-configuration/agents-md) and [subagent guide](https://learn.chatgpt.com/docs/agent-configuration/subagents). A successful TOML parse alone does not establish live discovery or reviewer execution.
