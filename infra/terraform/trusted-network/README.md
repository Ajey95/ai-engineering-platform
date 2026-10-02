# Trusted control-plane network

Provides a VPC with public load-balancer subnets and private ECS/RDS/EFS
subnets in at least two availability zones. Each private subnet uses a NAT
gateway in its own zone so provider, OIDC and GitHub calls can leave the
trusted plane without assigning public IPs to tasks. VPC flow logs retain
30 days in CloudWatch. This VPC is separate from `sandbox-network`, which
has no NAT or general internet route.

Supply disjoint CIDRs for every public/private subnet and the sandbox VPC.
The module validates shape, while an AWS plan and live routing/egress probes
are still required after account and region selection. NAT gateways and
cross-AZ capacity incur ongoing cost; quote them before deployment.
