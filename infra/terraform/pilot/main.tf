module "trusted_network" {
  source   = "../trusted-network"
  name     = var.name
  vpc_cidr = var.trusted_vpc_cidr
  zones    = var.trusted_zones
  tags     = var.tags
}

module "sandbox_artifacts" {
  source     = "../sandbox-artifacts"
  aws_region = var.aws_region
  name       = var.name
  tags       = var.tags
}

module "sandbox_network" {
  source              = "../sandbox-network"
  aws_region          = var.aws_region
  name                = var.name
  vpc_cidr            = var.sandbox_vpc_cidr
  subnets             = var.sandbox_zones
  artifact_bucket_arn = module.sandbox_artifacts.bucket_arn
  tags                = var.tags
}

module "queues" {
  source     = "../queues"
  aws_region = var.aws_region
  name       = var.name
  tags       = var.tags
}

module "edge" {
  source                         = "../edge"
  aws_region                     = var.aws_region
  name                           = var.name
  app_domain                     = var.app_domain
  api_origin_domain              = var.api_origin_domain
  cloudfront_certificate_arn     = var.cloudfront_certificate_arn
  cloudfront_public_key_pem_path = var.cloudfront_public_key_pem_path
  tags                           = var.tags
}

module "control_plane" {
  source                       = "../control-plane"
  aws_region                   = var.aws_region
  name                         = var.name
  vpc_id                       = module.trusted_network.vpc_id
  public_subnet_ids            = module.trusted_network.public_subnet_ids
  private_subnet_ids           = module.trusted_network.private_subnet_ids
  api_origin_domain            = var.api_origin_domain
  api_certificate_arn          = var.api_certificate_arn
  public_base_url              = "https://${var.app_domain}"
  image_uri                    = var.trusted_image_uri
  media_image_uri              = var.media_image_uri
  secret_environment           = var.secret_environment
  sandbox_artifact_bucket_arn  = module.sandbox_artifacts.bucket_arn
  sandbox_artifact_bucket_name = module.sandbox_artifacts.bucket_name
  private_media_bucket_arn     = module.edge.private_media_bucket_arn
  private_media_bucket_name    = module.edge.private_media_bucket
  media_publisher_policy_arn   = module.edge.media_publisher_policy_arn
  media_deletion_policy_arn    = module.edge.media_deletion_policy_arn
  cloudfront_distribution_id   = module.edge.cloudfront_distribution_id
  cloudfront_key_pair_id       = module.edge.cloudfront_key_pair_id
  agent_queue_arn              = module.queues.queue_arns["agent"]
  agent_queue_url              = module.queues.queue_urls["agent"]
  media_queue_arn              = module.queues.queue_arns["media"]
  media_queue_url              = module.queues.queue_urls["media"]
  sandbox_ami_id               = var.sandbox_ami_id
  sandbox_subnet_id            = module.sandbox_network.private_subnet_ids_by_az[var.sandbox_execution_az]
  sandbox_security_group_id    = module.sandbox_network.sandbox_security_group_id
  deploy_services              = var.deploy_services
  hosted_execution_enabled     = var.hosted_execution_enabled
  tags                         = var.tags
}

resource "aws_route53_record" "api_origin" {
  zone_id = var.hosted_zone_id
  name    = var.api_origin_domain
  type    = "A"
  alias {
    name                   = module.control_plane.api_alb_dns_name
    zone_id                = module.control_plane.api_alb_zone_id
    evaluate_target_health = true
  }
}

resource "aws_route53_record" "app" {
  zone_id = var.hosted_zone_id
  name    = var.app_domain
  type    = "A"
  alias {
    name                   = module.edge.cloudfront_domain_name
    zone_id                = module.edge.cloudfront_hosted_zone_id
    evaluate_target_health = false
  }
}
