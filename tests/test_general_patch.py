import hashlib
import io
import json
import tarfile

import pytest

from platform_app.general_patch import (
    GeneralPatchError,
    build_candidate_tree,
    parse_general_patch,
)
from platform_app.repository_archive import SourceArchive


def _source():
    data = b"def answer():\n    return 0\n"
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        member = tarfile.TarInfo("app.py")
        member.size = len(data)
        member.mode = 0o644
        archive.addfile(member, io.BytesIO(data))
    raw = output.getvalue()
    return SourceArchive("a" * 40, hashlib.sha256(raw).hexdigest(), raw, 1), data


def _response(path, base, content):
    return json.dumps({
        "diagnosis": "Wrong return value",
        "files": [{"path": path, "base_sha256": base, "content": content}],
    })


def test_general_candidate_pins_base_file_and_produces_review_diff(tmp_path):
    source, before = _source()
    after = "def answer():\n    return 1\n"
    proposal = parse_general_patch(
        _response("app.py", hashlib.sha256(before).hexdigest(), after),
        frozenset({"app.py"}),
    )
    candidate = build_candidate_tree(source, proposal, tmp_path)
    assert "-    return 0" in candidate.diff
    assert "+    return 1" in candidate.diff
    assert candidate.source.commit == source.commit
    assert candidate.source.sha256 != source.sha256
    with tarfile.open(fileobj=io.BytesIO(candidate.source.archive), mode="r:") as archive:
        assert archive.extractfile("app.py").read() == after.encode()
    assert list(tmp_path.iterdir()) == []


def test_general_patch_rejects_unapproved_or_stale_change(tmp_path):
    source, before = _source()
    digest = hashlib.sha256(before).hexdigest()
    with pytest.raises(GeneralPatchError, match="approved scope"):
        parse_general_patch(
            _response("../app.py", digest, "bad"), frozenset({"../app.py"})
        )
    with pytest.raises(GeneralPatchError, match="approved scope"):
        parse_general_patch(_response("app.py", digest, "bad"), frozenset())
    proposal = parse_general_patch(
        _response("app.py", "b" * 64, "changed"), frozenset({"app.py"})
    )
    with pytest.raises(GeneralPatchError, match="base file changed"):
        build_candidate_tree(source, proposal, tmp_path)
    unchanged = parse_general_patch(
        _response("app.py", digest, before.decode()), frozenset({"app.py"})
    )
    with pytest.raises(GeneralPatchError, match="no change"):
        build_candidate_tree(source, unchanged, tmp_path)
