"""Bounded, checksum listed archive of review packet and verified local evidence."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile

MAX_BUNDLE_BYTES = 20_000_000
NAME = re.compile(
    r"^(?:patch\.diff|screenshots/(?:baseline|candidate)\.png|"
    r"logs/(?:named|browser|oracle|candidate_named|candidate_browser|candidate_oracle)\.log)$"
)


class BundleError(Exception):
    pass


def build_evidence_bundle(packet: dict, artifacts: dict[str, bytes]) -> bytes:
    if not isinstance(packet, dict) or not isinstance(packet.get("run_id"), str):
        raise BundleError("Review packet is invalid")
    if any(not NAME.fullmatch(name) or not isinstance(content, bytes)
           for name, content in artifacts.items()):
        raise BundleError("Evidence item is outside the allowed archive scope")
    packet_bytes = json.dumps(packet, indent=2, sort_keys=True, default=str).encode("utf-8")
    files = {"review-packet.json": packet_bytes, **artifacts}
    if sum(len(content) for content in files.values()) > MAX_BUNDLE_BYTES:
        raise BundleError("Review evidence exceeds the archive limit")
    manifest = {
        "schema_version": "1.0", "run_id": packet["run_id"],
        "media_included": False,
        "files": [
            {"path": name, "bytes": len(content),
             "sha256": hashlib.sha256(content).hexdigest()}
            for name, content in sorted(files.items())
        ],
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            archive.writestr(name, content)
        archive.writestr("bundle-manifest.json", json.dumps(manifest, indent=2))
    return output.getvalue()
