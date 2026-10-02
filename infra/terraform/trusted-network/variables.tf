variable "name" {
  type = string
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{3,28}[a-z0-9]$", var.name))
    error_message = "Use a lowercase DNS-safe prefix of 5 to 30 characters."
  }
}

variable "vpc_cidr" {
  type        = string
  description = "Trusted control-plane VPC CIDR, disjoint from sandbox and customer networks."
}

variable "zones" {
  type = map(object({
    public_cidr  = string
    private_cidr = string
  }))
  description = "At least two availability-zone names with distinct public/private CIDRs."
  validation {
    condition = length(var.zones) >= 2 && length(distinct(flatten([
      for zone in values(var.zones) : [zone.public_cidr, zone.private_cidr]
    ]))) == length(var.zones) * 2
    error_message = "Provide two or more AZs and unique public/private CIDRs."
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}
