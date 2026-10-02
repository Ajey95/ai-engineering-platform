# Isolated guest network

This module provisions a dedicated IPv4 VPC with at least two private subnets.
It creates no internet gateway, NAT gateway, public IP route, IPv6 assignment or
inbound security group rule. Guest egress is limited to HTTPS addresses in the
regional S3 prefix list. A gateway endpoint permits only reads under
`sandbox/in/` and writes under `sandbox/out/` in the selected private bucket.

The guest has no IAM instance profile. A trusted broker must issue short-lived,
single-object presigned S3 URLs after recording the exact input/output hashes,
tenant, run and lease. The guest AMI must bake all language, browser and test
dependencies; this network cannot install from public registries. The broker
must launch into an output subnet/security group and verify the returned VM's
network settings. Run the AC-15 metadata, private-address, exfiltration and
malicious dependency tests in the chosen account before enabling customer runs.

The S3 endpoint policy narrows traffic through this endpoint; bucket policies,
presigned URL scope, KMS policy, per-run capability revocation, a hardened AMI,
image/version pinning and live network proof remain separate requirements.
Set `name`, `aws_region`, `vpc_cidr`, two availability-zone subnet CIDRs and the
private artifact bucket ARN, then run `terraform plan` in the selected AWS
account. No default account, region or CIDR is assumed.
