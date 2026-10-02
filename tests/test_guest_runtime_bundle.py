import hashlib
import io
import tarfile

from scripts.build_guest_runtime import build_runtime_bundle


def test_guest_runtime_bundle_is_deterministic_and_scoped(tmp_path):
    repository = tmp_path / "repository"
    package = repository / "platform_app"
    package.mkdir(parents=True)
    (repository / "pyproject.toml").write_text("[project]\nname='guest'\n")
    (repository / "uv.lock").write_text("version = 1\n")
    (package / "__init__.py").write_bytes(b"VALUE = 1\n")
    (repository / "secret.env").write_text("MUST_NOT_PACKAGE")
    first = tmp_path / "first.tar"
    second = tmp_path / "second.tar"
    digest = build_runtime_bundle(repository, first)
    assert build_runtime_bundle(repository, second) == digest
    assert first.read_bytes() == second.read_bytes()
    assert hashlib.sha256(first.read_bytes()).hexdigest() == digest
    with tarfile.open(fileobj=io.BytesIO(first.read_bytes()), mode="r:") as archive:
        assert archive.getnames() == [
            "pyproject.toml", "uv.lock", "platform_app/__init__.py",
        ]
        assert archive.extractfile("platform_app/__init__.py").read() == b"VALUE = 1\n"
