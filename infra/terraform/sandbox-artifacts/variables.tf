variable "aws_region" {
  type = string
}

variable "name" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.name))
    error_message = "Use a 3 to 31 character lowercase deployment prefix."
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}
