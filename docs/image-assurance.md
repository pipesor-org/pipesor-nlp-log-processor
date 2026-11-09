# Image assurance

The quality workflow builds the worker image without BuildKit provenance and
records its local content digest. It does not publish the image or modify Azure.

The runtime is checked in two independent scopes:

- Trivy `0.76.2`, pinned by image digest, generates an operating-system SBOM and
  blocks fixed `HIGH` or `CRITICAL` operating-system vulnerabilities.
- `pip-audit 2.10.1` generates a Python CycloneDX SBOM from the packages actually
  installed in the runtime image and blocks known Python dependency
  vulnerabilities.

The split is intentional. The digest-pinned upstream Python image includes an
OCI SBOM for its build environment. Scanning that attestation as if it described
the final runtime reports packages which are absent or upgraded in the final
filesystem. The workflow retains separate OS and Python evidence rather than
suppressing individual vulnerability identifiers.

Every quality run uploads the image digest, both CycloneDX SBOMs and both
vulnerability reports as a 14-day workflow artifact. The workflow also verifies
the non-root identity, safe failure before Azure client creation and delivery of
`SIGTERM` to the application's shutdown handler with the approved 60-second
grace period.

Private GHCR publication is authorized. After the quality job succeeds on a
`main` push, a separate least-privilege job downloads that exact validated image
and publishes it once as
`ghcr.io/pipesor-org/pipesor-nlp-log-processor:sha-<full-commit>`. The job records
the registry digest and fails unless the package API reports private visibility.
It does not publish `latest` or rebuild the image. Release tags must promote this
digest rather than rebuilding it.

Azure deployment, registry pull authorization and RBAC remain separate delivery
changes and are not authorized by image publication.
