import hashlib
import subprocess
from pathlib import Path

import pytest

from platform_app.code_navigation import SnapshotNavigator
from platform_app.development_worker import _pinned_fixture
from platform_app.service import ServiceError


def test_pinned_snapshot_navigation_stays_inside_fixture_base(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    _, workspace, oracle = _pinned_fixture(repository, commit, tmp_path / "archive")
    navigator = SnapshotNavigator(workspace, commit)
    listing = navigator.list_files(limit=100)
    assert "server.py" in listing["paths"]
    assert not any("oracle" in path for path in listing["paths"])
    search = navigator.search_text("quantity", max_matches=20)
    assert any(match["path"] == "server.py" for match in search["matches"])
    symbols = navigator.python_symbols("server.py")
    assert any(symbol["name"] == "create_ticket" for symbol in symbols["symbols"])
    excerpt = navigator.read_excerpt("server.py", max_lines=400)
    assert excerpt["sha256"] == hashlib.sha256((workspace / "server.py").read_bytes()).hexdigest()
    assert excerpt["text"] == (workspace / "server.py").read_bytes().decode("utf-8")
    assert excerpt["commit"] == commit
    assert oracle.is_file()
    with pytest.raises(ServiceError) as traversal:
        navigator.read_excerpt("../../../oracles/form-submit-001/test_hidden.py")
    assert traversal.value.code == "CODE_SCOPE_INVALID"


def test_navigation_rejects_oversized_and_binary_content(tmp_path):
    root = tmp_path / "snapshot"
    root.mkdir()
    (root / "huge.py").write_bytes(b"x" * 100_001)
    (root / "binary.py").write_bytes(b"\xff\xfe")
    navigator = SnapshotNavigator(root, "a" * 40)
    with pytest.raises(ServiceError) as oversized:
        navigator.read_excerpt("huge.py")
    assert oversized.value.code == "CODE_SCOPE_TOO_LARGE"
    with pytest.raises(ServiceError) as binary:
        navigator.python_symbols("binary.py")
    assert binary.value.code == "CODE_UNREADABLE"
    assert navigator.search_text("unmatched")["matches"] == []
