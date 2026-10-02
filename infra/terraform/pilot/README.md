# Single-region pilot stack

This root composes the trusted network, private sandbox network and bucket,
SQS queues, private web/media edge, ECS API/coordinator/publication services,
Multi-AZ RDS PostgreSQL, EFS artifact volume, and DNS aliases. It is a
deployment definition, not a deployed or qualified environment.

The account, region, domain, ACM certificates, AMI, immutable OCI image,
Secrets Manager ARNs and CIDR ranges are required inputs. Keep values in a
private `.tfvars` file or deployment system; do not commit secret values.
Use a pre-existing private state bucket and pass `-backend-config` from a
copy of `backend.hcl.example`. No credentials are stored in this repository.

Deployment order:

1. Select an account/region, private state bucket and domain. Build and
   qualify the sandbox AMI and trusted image. Create regional and us-east-1
   certificates plus the media public signing key.
2. Supply CIDRs, secret ARNs and other variables. Keep `deploy_services=false`
   and `hosted_execution_enabled=false`. Initialize, plan, review the plan,
   then apply infrastructure.
3. Create the application DB user and populate its `AIP_DATABASE_URL` secret.
   Run `alembic upgrade head` as a one-time trusted ECS task. Verify DB TLS,
   backup and restore before starting tasks.
4. Set `deploy_services=true`, apply, then check API health through CloudFront
   and direct origin, OIDC login, queue dispatch and private artifact access.
5. Challenge the sandbox VM and egress policy, qualify live provider/GitHub
   accounts and run a real hosted repository. Only then set
   `hosted_execution_enabled=true`.

The default root provisions no running ECS tasks and leaves hosted admission
off. The stack does not yet provision Memgraph, Redis, media transcode workers
or production alert collection, so it does not satisfy the full pilot gate.
