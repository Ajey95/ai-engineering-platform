variable "name" {
  type        = string
  description = "Deployment prefix, unique in the selected account."
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{3,28}[a-z0-9]$", var.name))
    error_message = "name must be a lowercase DNS-safe prefix of 5 to 30 characters."
  }
}

variable "aws_region" {
  type        = string
  description = "Region of the VPC and all regional services."
}

variable "vpc_id" {
  type        = string
  description = "Trusted control-plane VPC, separate from the no-NAT sandbox VPC."
}

variable "public_subnet_ids" {
  type        = list(string)
  description = "At least two public subnets in distinct availability zones for the ALB."
  validation {
    condition     = length(distinct(var.public_subnet_ids)) >= 2
    error_message = "Two distinct public subnets are required."
  }
}

variable "private_subnet_ids" {
  type        = list(string)
  description = "At least two private subnets with controlled NAT egress for ECS."
  validation {
    condition     = length(distinct(var.private_subnet_ids)) >= 2
    error_message = "Two distinct private subnets are required."
  }
}

variable "api_origin_domain" {
  type        = string
  description = "HTTPS origin hostname configured in the edge distribution."
}

variable "api_certificate_arn" {
  type        = string
  description = "Regional ACM certificate covering api_origin_domain."
}

variable "public_base_url" {
  type        = string
  description = "Customer-facing HTTPS URL used for browser OAuth callbacks."
  validation {
    condition     = startswith(var.public_base_url, "https://")
    error_message = "public_base_url must use HTTPS."
  }
}

variable "image_uri" {
  type        = string
  description = "Immutable trusted control-plane image URI with @sha256 digest."
  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.image_uri))
    error_message = "image_uri must pin an OCI image digest."
  }
}

variable "secret_environment" {
  type        = map(map(string))
  description = "Per-workload ECS env name to existing Secrets Manager secret ARN. See README for required maps."
  validation {
    condition = (alltrue([for workload in [
      "api", "agent", "run_relay", "sandbox_cleanup", "graph_projection",
      "operations", "alert_delivery", "publication", "media", "media_cleanup"
      ] :
      contains(keys(var.secret_environment), workload)
      ]) && alltrue([for workload in keys(var.secret_environment) :
      contains([
        "api", "agent", "run_relay", "sandbox_cleanup", "graph_projection",
        "operations", "alert_delivery", "publication", "media", "media_cleanup"
      ], workload)
      ]) && alltrue(flatten([for workload, secrets in var.secret_environment : [
        for name, arn in secrets :
        can(regex("^AIP_[A-Z0-9_]+$", name)) && startswith(arn, "arn:aws:secretsmanager:")
        ]])) && alltrue([for workload in [
        "api", "agent", "run_relay", "sandbox_cleanup", "graph_projection",
        "operations", "alert_delivery", "publication", "media", "media_cleanup"
      ] :
      contains(keys(lookup(var.secret_environment, workload, {})), "AIP_DATABASE_URL")
      ]) && alltrue([for name in [
        "AIP_OIDC_ISSUER", "AIP_OIDC_AUDIENCE", "AIP_OIDC_JWKS_URL",
        "AIP_OIDC_CLIENT_ID", "AIP_OIDC_CLIENT_SECRET",
        "AIP_OIDC_AUTHORIZATION_ENDPOINT", "AIP_OIDC_TOKEN_ENDPOINT",
        "AIP_BROWSER_SESSION_SECRET", "AIP_CLOUDFRONT_PRIVATE_KEY_B64",
      ] : contains(keys(lookup(var.secret_environment, "api", {})), name)]) &&
      contains(keys(lookup(var.secret_environment, "agent", {})), "AIP_SANDBOX_ENVELOPE_KEY_B64") &&
      contains(keys(lookup(var.secret_environment, "graph_projection", {})), "AIP_MEMGRAPH_URI") &&
      alltrue([for name in ["AIP_PAGER_WEBHOOK_URL", "AIP_PAGER_WEBHOOK_SECRET"] :
    contains(keys(lookup(var.secret_environment, "alert_delivery", {})), name)]))
    error_message = "Provide separate workload secret maps with database, API identity, agent envelope, graph URI and pager credentials."
  }
}

variable "media_image_uri" {
  type        = string
  description = "Immutable trusted FFmpeg worker image URI with @sha256 digest."
  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.media_image_uri))
    error_message = "media_image_uri must pin an OCI image digest."
  }
}

variable "sandbox_artifact_bucket_arn" {
  type = string
}

variable "sandbox_artifact_bucket_name" {
  type = string
}

variable "private_media_bucket_arn" {
  type = string
}

variable "private_media_bucket_name" {
  type = string
}

variable "media_publisher_policy_arn" {
  type = string
}

variable "media_deletion_policy_arn" {
  type = string
}

variable "cloudfront_distribution_id" {
  type = string
}

variable "cloudfront_key_pair_id" {
  type = string
}

variable "agent_queue_arn" {
  type = string
}

variable "agent_queue_url" {
  type = string
}

variable "media_queue_arn" {
  type = string
}

variable "media_queue_url" {
  type = string
}

variable "sandbox_ami_id" {
  type = string
}

variable "sandbox_subnet_id" {
  type = string
}

variable "sandbox_security_group_id" {
  type = string
}

variable "sandbox_instance_type" {
  type    = string
  default = "m6i.large"
}

variable "sandbox_root_device_name" {
  type    = string
  default = "/dev/xvda"
}

variable "db_instance_class" {
  type    = string
  default = "db.m6g.large"
}

variable "db_allocated_storage_gib" {
  type    = number
  default = 100
  validation {
    condition     = var.db_allocated_storage_gib >= 100
    error_message = "Paid-pilot database storage must be at least 100 GiB."
  }
}

variable "hosted_execution_enabled" {
  type        = bool
  default     = false
  description = "Enable only after live isolation, provider, identity and recovery qualification."
}

variable "deploy_services" {
  type        = bool
  default     = false
  description = "Start services only after the app DB secret is populated and Alembic has run."
}

variable "tags" {
  type    = map(string)
  default = {}
}
