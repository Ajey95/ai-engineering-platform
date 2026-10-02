variable "aws_region" {
  type        = string
  description = "Selected single-region pilot AWS region."
}

variable "name" {
  type        = string
  description = "Globally unique DNS-safe deployment prefix."
}

variable "hosted_zone_id" {
  type        = string
  description = "Existing Route 53 public hosted zone for app and API origin names."
}

variable "app_domain" {
  type = string
}

variable "api_origin_domain" {
  type = string
}

variable "api_certificate_arn" {
  type        = string
  description = "Regional ACM certificate for api_origin_domain."
}

variable "cloudfront_certificate_arn" {
  type        = string
  description = "us-east-1 ACM certificate for app_domain."
}

variable "cloudfront_public_key_pem_path" {
  type        = string
  description = "Public RSA key file for private media signed cookies."
}

variable "trusted_vpc_cidr" {
  type = string
}

variable "trusted_zones" {
  type = map(object({
    public_cidr  = string
    private_cidr = string
  }))
}

variable "sandbox_vpc_cidr" {
  type = string
}

variable "sandbox_zones" {
  type = map(object({ cidr_block = string }))
}

variable "sandbox_execution_az" {
  type        = string
  description = "One approved sandbox subnet AZ for the initial worker."
}

variable "sandbox_ami_id" {
  type        = string
  description = "Image built and security-qualified from infra/sandbox-ami."
}

variable "trusted_image_uri" {
  type        = string
  description = "Immutable trusted control-plane OCI image URI."
}

variable "secret_environment" {
  type        = map(map(string))
  description = "Per-workload Secrets Manager ARNs; see control-plane module."
}

variable "deploy_services" {
  type    = bool
  default = false
}

variable "hosted_execution_enabled" {
  type    = bool
  default = false
}

variable "tags" {
  type    = map(string)
  default = {}
}
