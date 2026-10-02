"""Publish a locally built React distribution to its private S3 origin."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import boto3

from platform_app.web_publish import WebPublishError, publish_web_build


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--dist", type=Path, default=Path("apps/web/dist"))
    parser.add_argument("--revision")
    args = parser.parse_args()
    revision = args.revision or subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True, timeout=10
    ).strip()
    try:
        result = publish_web_build(boto3.client("s3"), args.bucket, args.dist, revision)
    except WebPublishError as error:
        parser.error(str(error))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
