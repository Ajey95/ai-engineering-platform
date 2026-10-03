"""Inspect the synthetic development sandbox from inside a live container."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from platform_app.dev_sandbox import docker_browser_command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="aip-dev-sandbox:0.1.0")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    artifacts = root / "artifacts" / "container-security-check"
    artifacts.mkdir(parents=True, exist_ok=True)
    command = docker_browser_command(
        args.image,
        root / "benchmarks" / "fixtures" / "form-submit" / "base",
        root / "benchmarks" / "fixtures" / "form-submit" / "manifest.json",
        artifacts,
        "aip-dev-security-check",
        runtime="wsl",
    )
    image_index = command.index(args.image)
    probe = """
import json, os, pathlib, socket
checks = {}
checks['non_root'] = os.geteuid() != 0
checks['no_docker_socket'] = not pathlib.Path('/var/run/docker.sock').exists()
checks['no_host_drive'] = not pathlib.Path('/mnt/c').exists()
try:
    pathlib.Path('/workspace/security-probe').write_text('escape')
    checks['workspace_read_only'] = False
except OSError:
    checks['workspace_read_only'] = True
try:
    socket.create_connection(('169.254.169.254', 80), timeout=2)
    checks['metadata_network_denied'] = False
except OSError:
    checks['metadata_network_denied'] = True
status = pathlib.Path('/proc/self/status').read_text()
checks['no_effective_caps'] = 'CapEff:\\t0000000000000000' in status
checks['no_new_privileges'] = 'NoNewPrivs:\\t1' in status
print(json.dumps(checks))
"""
    completed = subprocess.run(
        [*command[:image_index + 1], "python", "-c", probe],
        capture_output=True, text=True, timeout=30,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr[-1000:])
    checks = json.loads(completed.stdout)
    (artifacts / "checks.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    print(json.dumps(checks))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
