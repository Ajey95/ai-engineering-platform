# Trusted media image

The FFmpeg image runs `scripts.dispatch_hosted_media` to consume the media
SQS queue and PostgreSQL outbox, encode staged guest WebM into aligned HLS
variants, and publish verified private objects. The same image can run
`scripts.dispatch_private_media_deletions` with a separate task role for S3
removal and CloudFront invalidation. Customer repository code is never
executed in this image. The image expects the shared private artifact EFS
volume and a migrated PostgreSQL URL.

The current local image built from revision
`70aa0e12a7f3966d4d947c53c47bbe922bb3e37a` as
`sha256:d6683e7550fb5073eaa51a1c7621a63f5623fabc2d0d8548826d968612234f41`.
It imported the worker modules and encoded a real browser WebM through
`platform_app.media.encode_hls` as the non-root user under no-network,
read-only, capability-dropped container flags on 2026-10-03.

Build and push an immutable image digest after selecting an account and
registry. The image has not consumed a live AWS queue or published in the
selected AWS environment.
