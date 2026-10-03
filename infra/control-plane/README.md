# Trusted control-plane image

`Dockerfile` packages the locked Python application, migrations and operator
scripts as a non-root container. It includes Git for exact pinned repository
archive fetches by the hosted worker. It is for the API and trusted control workers.
It does not install a browser or execute a customer repository; the
development-only fixture runner remains a different image.

Build with Docker Engine:

```powershell
wsl -d Ubuntu-24.04 -u root -- docker build -f /mnt/d/projects/aiplatform/infra/control-plane/Dockerfile -t aip-control-plane:0.1.1 /mnt/d/projects/aiplatform
```

The current local image built from revision
`c74dbb60aaf1c778ce21b08fd459f242e948f9c5` as
`sha256:d74d049c419e8bc754047231f462f2a27c92a82d9ba94487cae44116e7ba1885`.
Its non-root, read-only, capability-dropped smoke container served internal
health and readiness 200. These are local image checks, not hosted release
qualification.

The API defaults to port 8000. A hosted task must inject a migrated PostgreSQL
URL, validated OIDC settings, the public HTTPS base URL and scoped secret
references, and use a workload role. Run `alembic upgrade head` as a separate
controlled migration task before API rollout. The image by itself is not a
hosted release: customer-run admission still fails closed until dedicated VM
isolation, private artifact publication, live identity and
restore qualification are implemented.

The same trusted image can run `python -m scripts.consume_hosted_dispatch --serve`
with a separate workload role. It needs `AIP_RUN_QUEUE_URL`,
`AIP_SANDBOX_ARTIFACT_BUCKET`, `AIP_SANDBOX_AMI_ID`,
`AIP_SANDBOX_INSTANCE_TYPE`, `AIP_SANDBOX_SUBNET_ID`,
`AIP_SANDBOX_SECURITY_GROUP_ID`, `AIP_SANDBOX_ROOT_DEVICE_NAME`, and a
base64 encoded 32 byte `AIP_SANDBOX_ENVELOPE_KEY_B64` supplied by a secret
manager. Configure `AIP_ARTIFACT_DIR` on a durable private volume; the worker
persists model and review artifacts there. The API must use the same volume to
serve verified review diffs. `AIP_HOSTED_EXECUTION_ENABLED` defaults to false
and must only be enabled after the account's AMI, network, role, queue, model,
identity and recovery qualification gates pass. No such gate has passed yet.

Run `python -m scripts.dispatch_publications --serve` as a separate trusted
worker against the same migrated PostgreSQL database and private durable
`AIP_ARTIFACT_DIR`. The API commits an outbox event only after exact draft PR
approval. The worker reads a GitHub credential from the approved connection's
`secret://env/AIP_*` reference and reconciles a deterministic branch and draft
PR after an uncertain write. Approval never merges or deploys the patch.
