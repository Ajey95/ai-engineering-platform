"""Create the exact reviewed Python runtime source bundle for a guest AMI."""

from __future__ import annotations

import argparse
import hashlib
import io
import tarfile
from pathlib import Path


def build_runtime_bundle(repository: Path, output: Path) -> str:
    repository = repository.resolve(strict=True)
    files = [repository / "pyproject.toml", repository / "uv.lock"]
    files += sorted((repository / "platform_app").rglob("*.py"))
    if any(not path.is_file() or path.is_symlink() for path in files):
        raise ValueError("Guest runtime input is missing or linked")
    with output.open("wb") as stream, tarfile.open(fileobj=stream, mode="w") as tar:
        for path in files:
            relative = path.relative_to(repository).as_posix()
            data = path.read_bytes()
            if len(data) > 5_000_000:
                raise ValueError("Guest runtime source file exceeds policy")
            entry = tarfile.TarInfo(relative)
            entry.size = len(data)
            entry.mode = 0o644
            entry.mtime = 0
            entry.uid = entry.gid = 0
            tar.addfile(entry, io.BytesIO(data))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output", type=Path, default=Path("infra/sandbox-ami/guest-runtime.tar")
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    digest = build_runtime_bundle(args.repository, args.output)
    print(f"Guest runtime bundle SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
