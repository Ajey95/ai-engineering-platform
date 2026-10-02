terraform {
  required_version = ">= 1.9.0"
  backend "s3" {}
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.67.0, < 7.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}
