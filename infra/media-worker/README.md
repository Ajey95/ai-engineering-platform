# Trusted media image

The FFmpeg image runs `scripts.dispatch_hosted_media` to consume the media
SQS queue and PostgreSQL outbox, encode staged guest WebM into aligned HLS
variants, and publish verified private objects. The same image can run
`scripts.dispatch_private_media_deletions` with a separate task role for S3
removal and CloudFront invalidation. Customer repository code is never
executed in this image. The image expects the shared private artifact EFS
volume and a migrated PostgreSQL URL.

The local image built and started as non-root under no-network, read-only
container flags on 2026-10-03 (image ID
`sha256:c5dc71907d45858299ff729cb780ca6d5828fdcfb08cebe78e0c7a47151ae993`).
Build and push an immutable image digest after selecting an account and
registry. The image has not consumed a live AWS queue or published in the
selected AWS environment.
