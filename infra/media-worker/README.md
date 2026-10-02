# Trusted media image

The FFmpeg image runs `scripts.dispatch_hosted_media` to consume the media
SQS queue and PostgreSQL outbox, encode staged guest WebM into aligned HLS
variants, and publish verified private objects. The same image can run
`scripts.dispatch_private_media_deletions` with a separate task role for S3
removal and CloudFront invalidation. Customer repository code is never
executed in this image. The image expects the shared private artifact EFS
volume and a migrated PostgreSQL URL.

Build and push an immutable image digest only after selecting an account and
registry. The Dockerfile has not been built or exercised in the selected
AWS environment.
