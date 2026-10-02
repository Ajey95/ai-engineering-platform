import io
import tarfile

import pytest

from platform_app.safe_archive import UnsafeArchive, extract_regular_tar


def _tar(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, content, kind in entries:
            item = tarfile.TarInfo(name)
            if kind == "file":
                item.size = len(content)
                archive.addfile(item, io.BytesIO(content))
            elif kind == "dir":
                item.type = tarfile.DIRTYPE
                archive.addfile(item)
            elif kind == "link":
                item.type = tarfile.SYMTYPE
                item.linkname = "../outside"
                archive.addfile(item)
    return output.getvalue()


def test_extracts_regular_files_into_empty_workspace(tmp_path):
    archive = _tar([
        ("src", b"", "dir"),
        ("src/app.py", b"print('ok')\n", "file"),
    ])
    names = extract_regular_tar(
        archive, tmp_path / "workspace",
        permitted=lambda name: name.startswith("src"),
    )
    assert names == ["src", "src/app.py"]
    assert (tmp_path / "workspace/src/app.py").read_bytes() == b"print('ok')\n"


@pytest.mark.parametrize("entries", [
    [("../outside", b"secret", "file")],
    [("/absolute", b"secret", "file")],
    [("src\\escape", b"secret", "file")],
    [("src/CON.txt", b"secret", "file")],
    [("src/trailing. ", b"secret", "file")],
    [("src/link", b"", "link")],
    [("src/App.py", b"a", "file"), ("src/app.py", b"b", "file")],
    [("src", b"a", "file"), ("src/child", b"b", "file")],
])
def test_rejects_escape_links_collisions_and_file_parent(tmp_path, entries):
    with pytest.raises(UnsafeArchive):
        extract_regular_tar(_tar(entries), tmp_path / "workspace")
    assert not (tmp_path / "outside").exists()


def test_rejects_expansion_limits_and_unapproved_paths(tmp_path):
    archive = _tar([("src/data", b"0123456789", "file")])
    with pytest.raises(UnsafeArchive):
        extract_regular_tar(archive, tmp_path / "a", max_file_bytes=5)
    with pytest.raises(UnsafeArchive):
        extract_regular_tar(archive, tmp_path / "b", max_expanded_bytes=5)
    with pytest.raises(UnsafeArchive):
        extract_regular_tar(archive, tmp_path / "c", max_files=0)
    with pytest.raises(UnsafeArchive):
        extract_regular_tar(archive, tmp_path / "d", permitted=lambda _: False)


def test_refuses_nonempty_or_linked_destination(tmp_path):
    archive = _tar([("file", b"safe", "file")])
    target = tmp_path / "workspace"
    target.mkdir()
    (target / "keep").write_text("existing")
    with pytest.raises(UnsafeArchive):
        extract_regular_tar(archive, target)
    assert (target / "keep").read_text() == "existing"
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Directory symlinks are unavailable")
    with pytest.raises(UnsafeArchive):
        extract_regular_tar(archive, linked)
