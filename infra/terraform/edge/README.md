# Private media edge module

This module owns one CloudFront distribution on the application hostname. It
routes `/v1/*` to a reviewed HTTPS API origin, serves the static React build
from a private web bucket, and serves `/private-media/*` from a separate private
bucket. Hashed `/assets/*` files use the optimized cache policy; the HTML
entrypoint and API are uncached. Every media request requires a CloudFront signed cookie. Both S3 origins
use an origin access control that signs requests. The media bucket policy limits
CloudFront reads to the distribution ARN and `private-media/*` prefix.

It does not create an AWS account, DNS zone, ACM certificate, API load balancer,
ECS service, database, sandbox, model credentials, or workload IAM roles. Those
are separate deployment dependencies and must be reviewed before applying this
module. A local `terraform validate` confirms HCL and provider schema only; it
does not confirm AWS permissions, edge authorization, or playback.

## Inputs and integration

Supply a selected AWS region and unique lowercase `name`. `app_domain` must
point to the distribution after it deploys. `cloudfront_certificate_arn` must
identify an issued certificate in `us-east-1` for that hostname.
`api_origin_domain` must already resolve to the API load balancer and present a
valid HTTPS certificate for its own hostname. The application should use
`AIP_PUBLIC_BASE_URL=https://<app_domain>` and set its OIDC redirect URI to that
origin's `/v1/auth/callback` route.

Generate an RSA 2048 or stronger key pair outside Terraform. Supply only the
public PEM path as `cloudfront_public_key_pem_path`. Keep the private key in a
secret manager; its base64-encoded value is read by the API as
`AIP_CLOUDFRONT_PRIVATE_KEY_B64`. Use the module outputs for
`AIP_CLOUDFRONT_KEY_PAIR_ID`, `AIP_CLOUDFRONT_DISTRIBUTION_ID`, and
`AIP_PRIVATE_MEDIA_BUCKET`. The trusted publisher and deletion worker need
separate scoped S3 and CloudFront IAM permissions. The module emits publisher
and deletion policy ARNs for attachment to separate workload roles; the API
signer only needs its private key and no media bucket write permission. Do not
put the private key in Terraform variables, state, or a checked-in tfvars file.

The media bucket intentionally has no S3 object versioning. Current deletion
uses scoped object deletion and checks the live prefix; enabling versioning
would require the worker to remove every prior object version and delete
marker before recording deletion could be considered complete. The lifecycle
rule only aborts incomplete multipart uploads. Abandoned partial uploads and
backup restore propagation still need separate reconciliation.

## Local validation

```powershell
terraform init -backend=false -input=false
terraform fmt -check
terraform validate
```

Run these commands inside this directory. Do not use `terraform apply` until
the AWS account, region, DNS, certificates, IAM policies, state backend,
rollback process, and hosted acceptance plan are selected.

References: [CloudFront signed cookies](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-signed-cookies.html),
[S3 origin access control](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html),
and [CloudFront invalidation idempotency](https://docs.aws.amazon.com/cloudfront/latest/APIReference/API_CreateInvalidation.html).
