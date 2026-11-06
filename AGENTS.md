# Repository instructions

## Context by task

- Before implementation, read the [common backend conventions](https://github.com/pipesor-org/pipesor-docs/blob/main/docs/development/backend-conventions.md). Use the accessible pipesor-docs checkout when available, distinguishing local changes from integrated main. Apply only the conventions relevant to this service; they do not add tables, integrations or permissions.
- Use [README.md](README.md#development-and-verification) for setup, runnable commands and contribution policies. Add verified commands there when introducing application tooling; reference them rather than maintaining a second command list here.
- For preprocessing, parsing or semantic-signal changes, consult [architecture](docs/architecture.md) and the applicable current model/ADRs it links. Read ADR-007 together with ADR-010 and ADR-019: historical detector candidates are not current MVP capabilities. Follow references for the affected subject, not the entire documentation set for every edit.
- For Python dependency selection, parser configuration, embeddings access or delivery work, consult the relevant sections of the [deployment contract](docs/deployment-contract.md). Its development baseline records the authorized version-selection policy; its integration and data sections record existing boundaries and unresolved access.
- Treat local architecture summaries as navigation, not frozen specifications. Use the accessible `../../pipesor-docs/` checkout or linked sources on `main`, accounting for later accepted decisions and relevant uncommitted changes. Inspect uncommitted source changes separately; their presence does not establish integration or approval. Report unavailable or conflicting evidence when it affects the task. Documentation links must follow `main` across repositories and relative paths within this repository; a consulted revision may be recorded as text only.

## Implementation constraints

- Keep processing between the documented Service Bus input and synchronous correlation-service output. FastAPI does not authorize a new public business API. Do not add direct vector-store access, downstream-table writes or owned persistent tables to fill a processing gap.
- Use Drain3 for log parsing and structuring. Preserve sanitized content, provenance and execution/correlation context through preprocessing and output mapping. Do not merge unrelated execution context because log templates match, or expose sensitive input in parser errors and diagnostic logs.
- Keep semantic representations within the approved retrieval purpose. Do not introduce LogBERT, anomaly detection or a separate model-training pipeline. The Foundry foundation does not select an embedding model, dimension, SDK, application endpoint or grants, and does not authorize an undeclared direct provider call.
- Verify the selected FastAPI, Drain3 and transitive dependencies together against the authorized Python baseline in an isolated project environment. Do not silently replace the runtime or parser to bypass incompatibility; report the concrete conflict and continue independent work where possible.
- Check consumption, parsing and synchronous handoff together for behavior changes. Use the agreed message, error and re-delivery contracts; do not assume exactly-once processing, choose retry/settlement semantics by inference, or claim successful downstream delivery when it failed.
- A request to implement a local increment authorizes its required local code, tooling and checks. Do not create cloud resources, permissions or secrets, or modify neighboring repositories as a side effect. Keep credentials and sensitive evidence out of committed files and reported output.
- If input/output semantics, parser-state lifecycle or embedding requirements needed by the increment are unresolved, identify the missing agreement and any applicable pending decision. Continue independent parsing or scaffolding work; do not invent integration behavior or block the whole service because an unrelated decision remains open.

## Verification and handoff

Use the applicable checks documented in the README. Add behavior checks with the first executable increment, including material cases for malformed or empty logs, preserved execution context, sensitive-data exposure and failed downstream handoff. Use synthetic sanitized logs and isolated parser fixtures; mock remote integrations for local tests. Live broker, correlation-service or provider checks need their own agreed scope and access.

For documentation-only edits, check affected links and source coherence; parse TOML when changing agent configuration. Do not claim a build or integration passed before it exists and has run. Keep the documentation that owns a changed behavior or command current in the same change. Report what changed, checks actually performed and remaining blockers. For substantial work spanning sessions, keep a task-specific plan with scope, acceptance criteria and verification progress; a small edit needs no plan file.

When a focused review is warranted and delegation is authorized, `nlp_reviewer` checks sanitized inputs, parsing, context preservation, semantic scope and handoff behavior. Supply the task and affected paths. Its review supplements the main agent's verification and human review.
