import io
import subprocess
import tarfile

import pytest

from platform_app.repository_archive import (
    RepositoryArchiveError,
    archive_repository_commit,
)


def _git(repo, *args):
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def test_archives_exact_commit_without_dirty_checkout_or_git_credentials(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "AIP Test")
    _git(repo, "config", "user.email", "aip-test@example.test")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-q", "-m", "baseline")
    commit = _git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8", newline="\n")
    (repo / "untracked-secret.txt").write_text("never export", encoding="utf-8")
    bundle = archive_repository_commit(repo, commit)
    assert bundle.commit == commit and bundle.file_count == 1
    assert len(bundle.sha256) == 64
    with tarfile.open(fileobj=io.BytesIO(bundle.archive), mode="r:") as archive:
        assert archive.extractfile("app.py").read() == b"value = 1\n"
        assert "untracked-secret.txt" not in archive.getnames()
    with pytest.raises(RepositoryArchiveError):
        archive_repository_commit(repo, "0" * 40)
    with pytest.raises(RepositoryArchiveError):
        archive_repository_commit(repo, commit, max_archive_bytes=512)


def test_rejects_unpinned_or_missing_repository(tmp_path):
    with pytest.raises(RepositoryArchiveError):
        archive_repository_commit(tmp_path / "missing", "a" * 40)
    with pytest.raises(RepositoryArchiveError):
        archive_repository_commit(tmp_path, "HEAD")
