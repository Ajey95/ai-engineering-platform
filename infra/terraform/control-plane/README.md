# Trusted ECS control plane (FR-DAT-01, FR-SBX-02, FR-SEC-03)

This Terraform module defines two API tasks, two hosted coordinator tasks,
one run-outbox relay, one sandbox cleanup worker, one Memgraph projector,
one operations evaluator, one pager delivery worker, one draft publication
task, two media encoding tasks and one media deletion task, a TLS application
load balancer, a Multi-AZ RDS
PostgreSQL instance, and an encrypted EFS access point shared by trusted
tasks. Task definitions pin an OCI image digest. All tasks run without public
IPs; the supplied trusted VPC must provide controlled NAT egress for provider,
GitHub and OIDC calls. The sandbox VPC remains separate and has no NAT route.

The module is intentionally parameterized by region, subnets, certificates,
image digest, sandbox network, queue and bucket identities. No account or
region has been selected. `terraform validate` checks syntax only; it does
not prove AWS permissions, IAM policy behavior, networking or guest isolation.

Before applying, create Secrets Manager secrets for at least
`AIP_DATABASE_URL`, `AIP_OIDC_ISSUER`, `AIP_OIDC_AUDIENCE`,
`AIP_OIDC_JWKS_URL`, OIDC client ID/secret and authorization/token endpoints,
`AIP_BROWSER_SESSION_SECRET`, `AIP_CLOUDFRONT_PRIVATE_KEY_B64` and
`AIP_SANDBOX_ENVELOPE_KEY_B64`. Supply their ARNs through the separate
`secret_environment.api`, `.agent`, `.run_relay`, `.sandbox_cleanup`,
`.graph_projection`, `.operations`, `.alert_delivery`, `.publication`,
`.media` and `.media_cleanup` maps. API OIDC/session
secrets belong only to `api`; the envelope and provider keys belong only to
`agent`; GitHub credentials used for fetch and publication belong to both
`agent` and `publication`. The graph projector needs `AIP_MEMGRAPH_URI` and
optional Memgraph user/password secrets. Pager delivery needs
`AIP_PAGER_WEBHOOK_URL` and `AIP_PAGER_WEBHOOK_SECRET`. Media, relay,
cleanup and operations tasks need the database secret; the relay and cleanup
receive narrow queue/EC2/S3 policies. The graph instance or managed service
must be provisioned privately and qualified separately; this module starts
only its projector. The edge module grants media tasks scoped S3 and
CloudFront policies. The app
database URL must identify a dedicated application user
with the migrated schema, not the RDS master user. Populate that secret after
RDS creation and before starting ECS services. `deploy_services` defaults
to false so the first apply provisions infrastructure with zero running
tasks. Run Alembic as a separate one-time migration task, then set
`deploy_services = true` and apply again.

`hosted_execution_enabled` defaults to false. Set it true only after the
selected account passes the VM isolation, identity, provider, repository and
restore gates. The EFS volume stores private run/model/review artifacts for
API and workers; its backup and restore behavior still needs qualification.

The output ALB DNS name must receive an `api_origin_domain` DNS record and
regional ACM certificate. Configure the edge module to use that HTTPS origin.
The ALB currently accepts public HTTPS, so production rollout also needs a
verified CloudFront origin restriction/WAF policy before a paid pilot.
