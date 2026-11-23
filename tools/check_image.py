"""Check the built worker image locally, without credentials or Azure access.

Usage: python3 tools/check_image.py <image>

Verifies the non-root UID/GID, the safe configuration failure before any Azure client is
created (exit code 2 with a configuration_invalid event) and delivery of SIGTERM to the
application's shutdown handler within the approved 60-second grace period.
"""

import subprocess
import sys
import time
import uuid

SHUTDOWN_PROBE = (
    "import itertools, time; from threading import Event; "
    "from pipesor_nlp_log_processor.main import install_shutdown_handlers; "
    "stop = Event(); install_shutdown_handlers(stop); print('signal_handler_ready', flush=True); "
    "all(time.sleep(0.1) or not stop.is_set() for _ in itertools.count())"
)


def docker(*args, check=True):
    return subprocess.run(["docker", *args], text=True, capture_output=True, check=check)


def require(condition, message):
    if not condition:
        raise SystemExit(f"Image check failed: {message}")


def check_image(image):
    user = docker("image", "inspect", image, "--format", "{{.Config.User}}").stdout.strip()
    require(user == "10001:10001", f"expected user 10001:10001, found {user!r}")

    startup = docker("run", "--rm", "--network", "none", image, check=False)
    require(startup.returncode == 2, f"unconfigured startup must exit 2, got {startup.returncode}")
    require('"event":"configuration_invalid"' in startup.stdout + startup.stderr,
            "unconfigured startup must report configuration_invalid")

    name = f"pipesor-nlp-check-{uuid.uuid4().hex[:12]}"
    try:
        docker("run", "--detach", "--name", name, "--network", "none", "--entrypoint", "python",
               image, "-c", SHUTDOWN_PROBE)
        for _ in range(30):
            if "signal_handler_ready" in docker("logs", name, check=False).stdout:
                break
            time.sleep(1)
        require("signal_handler_ready" in docker("logs", name, check=False).stdout,
                "shutdown handler did not become ready")
        docker("stop", "--timeout", "60", name)
        exit_code = docker("inspect", name, "--format", "{{.State.ExitCode}}").stdout.strip()
        require(exit_code == "0", f"SIGTERM must stop the worker cleanly, exit code {exit_code}")
    finally:
        docker("rm", "--force", name, check=False)
    print("Image checks passed: non-root 10001, safe configuration failure, graceful SIGTERM")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    check_image(sys.argv[1])
