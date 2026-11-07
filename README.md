# pipesor-nlp-log-processor

NLP Log Processor preprocesses protected logs, structures them with Drain3, prepares semantic signals and delivers processed evidence to the correlation service. Its canonical architecture ID is `nlp_log_processor`; the planned runtime is Python / FastAPI on Azure Container Apps.

The first local processing increment is implemented as a Python package. It validates required execution context, redacts common credential shapes before parsing, structures sanitized logs with an in-memory Drain3 adapter and preserves provenance and execution/correlation identifiers in the result. It does not consume Service Bus, call Azure OpenAI, deliver to the correlation service or expose a business HTTP API yet.

The Azure OpenAI embedding model, dimension, endpoint/SDK and grants remain pending decisions. Parser-state persistence and broker settlement/re-delivery semantics are also intentionally not selected by this increment.

## Documentation

- [Documentation baseline](docs/documentation-baseline.md): audited source revisions, evidence limits and adoption after merge.

- [Architecture](docs/architecture.md): service boundaries, integration context and current sources in `pipesor-docs`.
- [Deployment contract](docs/deployment-contract.md): development baseline, delivery requirements and unresolved contracts.
- [Integration contracts](docs/integration-contracts.md): candidate Service Bus input, Correlation handoff, delivery semantics, authentication and Drain3 lifecycle pending cross-service approval.
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

The package has no startup command yet because its documented input is Azure Service Bus and the broker contract remains pending. FastAPI does not by itself authorize a public or inbound business endpoint.

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
