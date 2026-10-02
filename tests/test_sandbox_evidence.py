import io
import tarfile

from platform_app.sandbox_evidence import package_guest_evidence, read_guest_evidence


def test_unprivileged_packager_and_single_file_reader(tmp_path):
    artifacts = tmp_path / "artifacts"
    nested = artifacts / "browser"
    nested.mkdir(parents=True)
    (artifacts / "result.json").write_bytes(b'{"status":"BASELINE_RECORDED"}')
    (nested / "final.png").write_bytes(b"png")
    destination = package_guest_evidence(artifacts)
    assert destination.name == "evidence.tar"
    raw = read_guest_evidence(artifacts)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        assert archive.getnames() == ["browser/final.png", "result.json"]
        assert archive.extractfile("result.json").read() == (
            b'{"status":"BASELINE_RECORDED"}'
        )


def test_missing_guest_evidence_yields_empty_valid_tar(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    raw = read_guest_evidence(tmp_path)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        assert archive.getnames() == []
