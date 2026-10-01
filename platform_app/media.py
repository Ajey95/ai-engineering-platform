"""Local private artifact encoder. Hosted object and CDN publication remain separate."""

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class MediaError(Exception):
    pass


@dataclass(frozen=True)
class VideoInfo:
    duration_seconds: float
    width: int
    height: int
    frame_rate: float
    byte_size: int


PROFILE = (("low", 360, 350_000), ("medium", 720, 1_200_000), ("high", 1080, 2_500_000))
PROFILE_REVISION = "hls-v1"


def probe(source: Path) -> VideoInfo:
    if not source.is_file():
        raise MediaError("Source recording is missing")
    if source.stat().st_size > 250_000_000:
        raise MediaError("Source recording exceeds 250 MB")
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate:format=duration",
            "-of",
            "json",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise MediaError("Source recording could not be decoded")
    try:
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        num, den = map(int, stream["r_frame_rate"].split("/"))
        info = VideoInfo(
            float(payload["format"]["duration"]),
            int(stream["width"]),
            int(stream["height"]),
            num / den,
            source.stat().st_size,
        )
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError) as error:
        raise MediaError("Source recording metadata is invalid") from error
    if info.duration_seconds <= 0 or info.duration_seconds > 900:
        raise MediaError("Source duration is outside the 15 minute limit")
    if info.width <= 0 or info.height <= 0 or info.frame_rate <= 0:
        raise MediaError("Source video dimensions or frame rate are invalid")
    return info


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def encode_hls(
    source: Path,
    artifact_root: Path,
    tenant_id: str,
    run_id: str,
    encoder_digest: str = "local-ffmpeg",
) -> Path:
    """Publish only complete variants; return immutable local master playlist path."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tenant_id) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,64}", run_id
    ):
        raise MediaError("Invalid artifact scope")
    source = source.resolve()
    info = probe(source)
    recording_hash = sha256_file(source)
    effect_key = hashlib.sha256(
        f"{recording_hash}:{PROFILE_REVISION}:{encoder_digest}".encode()
    ).hexdigest()
    base = artifact_root.resolve() / tenant_id / run_id / "media"
    target = base / effect_key
    master = target / "master.m3u8"
    if master.is_file():
        _publish_pointer(base, effect_key)
        return master
    base.mkdir(parents=True, exist_ok=True)
    variants = [
        (name, min(height, info.height), bitrate)
        for name, height, bitrate in PROFILE
        if height <= info.height
    ]
    if not variants:
        variants = [("source", info.height, 350_000)]
    with tempfile.TemporaryDirectory(prefix="hls-stage-", dir=base) as staging_path:
        stage = Path(staging_path)
        master_lines = ["#EXTM3U", "#EXT-X-VERSION:7"]
        for name, height, bitrate in variants:
            variant_dir = stage / name
            variant_dir.mkdir()
            playlist = variant_dir / "index.m3u8"
            gop = max(1, round(info.frame_rate * 2))
            command = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-an",
                "-vf",
                f"scale=-2:{height}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "veryfast",
                "-b:v",
                str(bitrate),
                "-maxrate",
                str(bitrate),
                "-bufsize",
                str(bitrate * 2),
                "-g",
                str(gop),
                "-keyint_min",
                str(gop),
                "-sc_threshold",
                "0",
                "-force_key_frames",
                "expr:gte(t,n_forced*2)",
                "-hls_time",
                "2",
                "-hls_playlist_type",
                "vod",
                "-hls_segment_type",
                "fmp4",
                "-hls_fmp4_init_filename",
                "init.mp4",
                "-hls_segment_filename",
                "segment_%04d.m4s",
                "index.m3u8",
            ]
            result = subprocess.run(
                command,
                cwd=variant_dir,
                capture_output=True,
                text=True,
                timeout=max(120, int(info.duration_seconds * 10)),
                check=False,
            )
            if result.returncode != 0:
                raise MediaError(f"FFmpeg failed for {name}: {result.stderr[-1000:]}")
            lines = playlist.read_text(encoding="utf-8")
            if "#EXT-X-ENDLIST" not in lines or "#EXTINF" not in lines:
                raise MediaError(f"Incomplete HLS playlist for {name}")
            segments = list(variant_dir.glob("segment_*.m4s"))
            if not segments or not (variant_dir / "init.mp4").is_file():
                raise MediaError(f"Incomplete HLS segments for {name}")
            width = int(round(info.width * height / info.height / 2) * 2)
            master_lines.extend(
                [
                    f"#EXT-X-STREAM-INF:BANDWIDTH={bitrate},RESOLUTION={width}x{height}",
                    f"{name}/index.m3u8",
                ]
            )
        (stage / "master.m3u8").write_text("\n".join(master_lines) + "\n", encoding="utf-8")
        if target.exists():
            if not master.is_file():
                raise MediaError("Existing media target is incomplete")
            _publish_pointer(base, effect_key)
            return master
        os.replace(stage, target)
    _publish_pointer(base, effect_key)
    return master


def _publish_pointer(base: Path, effect_key: str) -> None:
    pointer = base / "ready.json"
    pointer_tmp = base / "ready.json.tmp"
    pointer_tmp.write_text(
        json.dumps({"effect_key": effect_key, "master": f"{effect_key}/master.m3u8"}),
        encoding="utf-8",
    )
    os.replace(pointer_tmp, pointer)
