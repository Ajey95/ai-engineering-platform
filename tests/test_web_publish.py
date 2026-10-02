import base64
import hashlib

import pytest
from botocore.exceptions import ClientError

from platform_app.web_publish import WebPublishError, publish_web_build


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.writes = []
        self.race_index = False

    def head_object(self, **args):
        value = self.objects[args["Key"]]
        return {
            "ChecksumSHA256": value["ChecksumSHA256"],
            "ContentLength": len(value["Body"]),
            "ETag": value["ETag"],
        }

    def put_object(self, **args):
        key = args["Key"]
        if key == "index.html" and self.race_index:
            self.race_index = False
            self.objects[key] = self._stored({
                "Key": key, "Body": b"concurrent release",
                "ChecksumSHA256": "different",
            })
        old = self.objects.get(key)
        if (args.get("IfNoneMatch") == "*" and old is not None) or (
            args.get("IfMatch") is not None and (
                old is None or old["ETag"] != args["IfMatch"]
            )
        ):
            raise ClientError(
                {"Error": {"Code": "PreconditionFailed", "Message": "conflict"}},
                "PutObject",
            )
        self.objects[key] = self._stored(args)
        self.writes.append(key)

    @staticmethod
    def _stored(args):
        return {**args, "ETag": '"' + hashlib.md5(args["Body"]).hexdigest() + '"'}


def _dist(tmp_path):
    root = tmp_path / "dist"
    assets = root / "assets"
    assets.mkdir(parents=True)
    (root / "index.html").write_text(
        '<html><script src="/assets/index-abc12345.js"></script>'
        '<link href="/assets/index-def67890.css" rel="stylesheet"></html>',
        encoding="utf-8",
    )
    (assets / "index-abc12345.js").write_text("console.log('ready')", encoding="utf-8")
    (assets / "index-def67890.css").write_text("body { color: black }", encoding="utf-8")
    return root


def test_web_release_uploads_verified_assets_and_snapshot_before_index(tmp_path):
    root = _dist(tmp_path)
    client = FakeS3()
    revision = "a" * 40
    result = publish_web_build(client, "web-private", root, revision)
    assert result["revision"] == revision and result["asset_count"] == 2
    assert client.writes[-1] == "index.html"
    assert client.writes[-3:-1] == [
        f"releases/{revision}/index.html", f"releases/{revision}/manifest.json"
    ]
    assert all(key.startswith("assets/") for key in client.writes[:-3])
    assert client.objects["index.html"]["CacheControl"] == "no-store"
    for key, value in client.objects.items():
        expected = base64.b64encode(hashlib.sha256(value["Body"]).digest()).decode()
        assert value["ChecksumSHA256"] == expected, key
        assert value["ServerSideEncryption"] == "AES256"
    writes = len(client.writes)
    again = publish_web_build(client, "web-private", root, revision)
    assert again == result and client.writes[writes:] == ["index.html"]


def test_web_release_fails_before_upload_if_index_ref_is_missing(tmp_path):
    root = _dist(tmp_path)
    (root / "assets/index-abc12345.js").unlink()
    client = FakeS3()
    with pytest.raises(WebPublishError, match="missing compiled assets"):
        publish_web_build(client, "web-private", root, "a" * 40)
    assert not client.writes


def test_web_release_cannot_overwrite_immutable_asset_or_concurrent_index(tmp_path):
    root = _dist(tmp_path)
    client = FakeS3()
    asset = "assets/index-abc12345.js"
    client.objects[asset] = client._stored({
        "Key": asset, "Body": b"different", "ChecksumSHA256": "different",
    })
    with pytest.raises(WebPublishError, match="Immutable asset differs"):
        publish_web_build(client, "web-private", root, "a" * 40)
    assert "index.html" not in client.objects
    client.objects.clear()
    publish_web_build(client, "web-private", root, "a" * 40)
    client.race_index = True
    with pytest.raises(WebPublishError, match="Another release changed"):
        publish_web_build(client, "web-private", root, "a" * 40)
    assert client.objects["index.html"]["Body"] == b"concurrent release"
