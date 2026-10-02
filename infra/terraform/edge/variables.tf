variable "aws_region" {
  type        = string
  description = "Chosen residency region for the private S3 origins."
}

variable "name" {
  type        = string
  description = "Globally unique lowercase deployment prefix."
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{3,28}[a-z0-9]$", var.name))
    error_message = "name must be a lowercase DNS-safe prefix of 5 to 30 characters."
  }
}

variable "app_domain" {
  type        = string
  description = "Public application hostname covered by the CloudFront certificate."
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]+\\.[a-z]{2,}$", var.app_domain))
    error_message = "app_domain must be a DNS hostname."
  }
}

variable "cloudfront_certificate_arn" {
  type        = string
  description = "Issued ACM certificate ARN in us-east-1 for app_domain."
  validation {
    condition     = can(regex("^arn:aws:acm:us-east-1:[0-9]{12}:certificate/", var.cloudfront_certificate_arn))
    error_message = "CloudFront requires an issued us-east-1 ACM certificate."
  }
}

variable "api_origin_domain" {
  type        = string
  description = "HTTPS API origin hostname, with a matching TLS certificate on its load balancer."
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]+\\.[a-z]{2,}$", var.api_origin_domain))
    error_message = "api_origin_domain must be a DNS hostname."
  }
}

variable "cloudfront_public_key_pem_path" {
  type        = string
  description = "Path to RSA public key PEM. The private signing key must never enter Terraform state."
}

variable "price_class" {
  type    = string
  default = "PriceClass_100"
  validation {
    condition     = contains(["PriceClass_100", "PriceClass_200", "PriceClass_All"], var.price_class)
    error_message = "price_class must be a CloudFront price class."
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}
