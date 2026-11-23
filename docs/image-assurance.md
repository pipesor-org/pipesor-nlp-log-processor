# Image assurance

CI uses the shared [pipesor-ci](https://github.com/pipesor-org/pipesor-ci) pipeline. Every pull request and every
push to `main` builds the worker image once (without BuildKit provenance, labelled with the source revision) and
checks it before anything is published:

- [`tools/check_image.py`](../tools/check_image.py) verifies UID/GID `10001`, the safe failure before Azure client
  creation (exit code `2` with a `configuration_invalid` event) and delivery of `SIGTERM` to the application's
  shutdown handler within the approved 60-second grace period.
- Trivy `0.76.2`, pinned by image digest, scans the exact image archive (operating-system and Python packages in
  the final filesystem). It records a CycloneDX SBOM and a complete JSON vulnerability report as the 90-day
  `image-assurance-<run>-<attempt>` artifact and blocks `HIGH` or `CRITICAL` vulnerabilities that have a fixed
  version. Reviewed exceptions belong in a root `.trivyignore.yaml` with their justification.

Scanning the saved archive covers the final filesystem only; it does not read the upstream base image's build-time
SBOM attestation, which previously reported packages absent from the runtime image.

After the checks succeed on a `main` push, the shared publisher loads that exact archive, verifies its checksum and
revision label, keeps the GHCR package private and pushes it once as
`ghcr.io/pipesor-org/pipesor-nlp-log-processor:sha-<full-commit>-<run>-<attempt>`. It records `candidate.json`
(source SHA, image, digest, CI run/attempt) for 90 days. It does not publish `latest` or rebuild the image. Releases
and environments use that digest.

Azure deployment is owned by the IaC Terraform root, which pins the image by digest; registry pull authorization and
RBAC remain IaC changes.
