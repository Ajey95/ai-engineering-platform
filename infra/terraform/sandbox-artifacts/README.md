# Transient sandbox artifact bucket

This module creates the private versioned bucket used by the guest handoff.
It blocks public access, enforces HTTPS and explicit AES256 encryption headers,
retains transient `sandbox/` objects for seven days, expires old versions and
aborts incomplete multipart uploads. The control plane must copy accepted
evidence into its durable artifact store before expiry. `force_destroy` is
false so Terraform cannot silently remove retained objects.

Pass `bucket_arn` to the `sandbox-network` module's `artifact_bucket_arn`.
Grant the trusted broker a narrow prefix policy for staging, checksum reads,
conditional go/tombstone writes and cleanup. The guest receives presigned
single-object URLs, no AWS instance role. IAM roles, selected account/region,
bucket plan/apply, live signature checks and restore behavior still require
qualification.
