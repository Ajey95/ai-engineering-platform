variable "aws_region" {
  description = "Selected single-region pilot AWS region."
  type        = string
}

variable "name" {
  description = "Lowercase deployment prefix for sandbox network resources."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.name))
    error_message = "Use a 3 to 31 character lowercase deployment prefix."
  }
}

variable "vpc_cidr" {
  description = "Dedicated IPv4 CIDR for isolated sandbox VPC."
  type        = string
}

variable "subnets" {
  description = "Two or more private sandbox subnets keyed by availability zone."
  type = map(object({
    cidr_block = string
  }))
  validation {
    condition     = length(var.subnets) >= 2
    error_message = "Provide at least two availability zones."
  }
}

variable "artifact_bucket_arn" {
  description = "Private S3 bucket ARN holding presigned sandbox inputs and outputs."
  type        = string
  validation {
    condition     = can(regex("^arn:aws:s3:::[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.artifact_bucket_arn))
    error_message = "Use an S3 bucket ARN without an object suffix."
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}
