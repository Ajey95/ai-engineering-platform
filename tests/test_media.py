import json
import shutil
import subprocess

import pytest

from platform_app.media import MediaError, encode_hls, probe


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg is required for the media integration test",
)
def test_media_publication_is_complete_and_idempotent(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=12",
            "-t",
            "3",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        timeout=60,
    )
    assert probe(source).height == 360
    root = tmp_path / "private"
    first = encode_hls(source, root, "tenant-a", "run-a")
    second = encode_hls(source, root, "tenant-a", "run-a")
    monkeypatch.chdir(tmp_path)
    relative = encode_hls(source.relative_to(tmp_path), root, "tenant-a", "run-a")
    assert first == second
    assert first == relative
    text = first.read_text(encoding="utf-8")
    assert "low/index.m3u8" in text
    assert "medium/index.m3u8" not in text
    assert "high/index.m3u8" not in text
    pointer = json.loads((first.parent.parent / "ready.json").read_text(encoding="utf-8"))
    assert pointer["master"].endswith("master.m3u8")
    assert (first.parent / "low" / "init.mp4").is_file()
    assert list((first.parent / "low").glob("segment_*.m4s"))


def test_artifact_scope_cannot_escape_root(tmp_path):
    with pytest.raises(MediaError, match="Invalid artifact scope"):
        encode_hls(tmp_path / "missing.mp4", tmp_path, "../tenant", "run-a")
