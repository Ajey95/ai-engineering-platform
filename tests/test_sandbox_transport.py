import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

from platform_app.sandbox_transport import (
    SandboxObjectKeys,
    SandboxTransportError,
    fence_guest_outputs,
    issue_guest_urls,
    publish_guest_go,
    stage_source_archive,
)


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, **kwargs):
        key = kwargs["Key"]
        if kwargs.get("IfNoneMatch") == "*" and key in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        data = kwargs["Body"]
        checksum = base64.b64encode(hashlib.sha256(data).digest()).decode()
        if kwargs.get("ChecksumSHA256") not in (None, checksum):
            raise AssertionError("Bad upload checksum")
        self.objects[key] = (data, checksum, kwargs)
        return {"ChecksumSHA256": checksum}

    def head_object(self, **kwargs):
        data, checksum, _ = self.objects[kwargs["Key"]]
        return {"ContentLength": len(data), "ChecksumSHA256": checksum}


def _keys():
    return SandboxObjectKeys.scoped("tenant-a", "project-a", "run-a", "lease-a")


def test_source_staging_is_immutable_checksum_verified_and_scoped():
    s3 = FakeS3()
    keys = _keys()
    payload = b"tar fixture bytes"
    digest = stage_source_archive(s3, "private-bucket", keys, payload)
    assert digest == hashlib.sha256(payload).hexdigest()
    assert stage_source_archive(s3, "private-bucket", keys, payload) == digest
    assert s3.objects[keys.source][2]["ServerSideEncryption"] == "AES256"
    with pytest.raises(SandboxTransportError):
        stage_source_archive(s3, "private-bucket", keys, b"different bytes")
    with pytest.raises(SandboxTransportError):
        SandboxObjectKeys.scoped("tenant/other", "project-a", "run-a", "lease-a")


def test_presigned_urls_bind_each_exact_operation_and_conditional_put():
    s3 = boto3.client(
        "s3", region_name="us-east-1", aws_access_key_id="fixture-key",
        aws_secret_access_key="fixture-secret",
        config=Config(signature_version="s3v4"),
    )
    urls = issue_guest_urls(s3, "example-bucket", _keys(), ttl_seconds=300)
    for url in (urls.source_get, urls.ready_put, urls.go_get, urls.result_put):
        parsed = urlsplit(url)
        assert parsed.scheme == "https"
        query = parse_qs(parsed.query)
        assert query["X-Amz-Expires"] == ["300"]
        assert query["X-Amz-Signature"]
    for url in (urls.ready_put, urls.result_put):
        headers = parse_qs(urlsplit(url).query)["X-Amz-SignedHeaders"][0].split(";")
        assert "if-none-match" in headers
        assert "x-amz-server-side-encryption" in headers
    with pytest.raises(SandboxTransportError):
        issue_guest_urls(s3, "example-bucket", _keys(), ttl_seconds=3600)


def test_go_marker_is_idempotent_and_revocation_fences_unused_output_slots():
    s3 = FakeS3()
    keys = _keys()
    source = "a" * 64
    first = publish_guest_go(s3, "private-bucket", keys, "lease-a", 3, source)
    assert publish_guest_go(s3, "private-bucket", keys, "lease-a", 3, source) == first
    with pytest.raises(SandboxTransportError):
        publish_guest_go(s3, "private-bucket", keys, "lease-a", 4, source)
    fence_guest_outputs(s3, "private-bucket", keys)
    fence_guest_outputs(s3, "private-bucket", keys)
    assert s3.objects[keys.ready][0] == b""
    assert s3.objects[keys.result][0] == b""
    with pytest.raises(ClientError):
        s3.put_object(
            Bucket="private-bucket", Key=keys.result, Body=b"late",
            IfNoneMatch="*", ServerSideEncryption="AES256",
        )
