locals {
  tags = merge(var.tags, { Application = "ai-engineering-platform", Boundary = "sandbox" })
}

data "aws_prefix_list" "s3" {
  name = "com.amazonaws.${var.aws_region}.s3"
}

resource "aws_vpc" "sandbox" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.tags, { Name = "${var.name}-sandbox" })
}

resource "aws_subnet" "sandbox" {
  for_each                        = var.subnets
  vpc_id                          = aws_vpc.sandbox.id
  availability_zone               = each.key
  cidr_block                      = each.value.cidr_block
  map_public_ip_on_launch         = false
  assign_ipv6_address_on_creation = false
  tags                            = merge(local.tags, { Name = "${var.name}-sandbox-${each.key}" })
}

resource "aws_route_table" "sandbox" {
  for_each = var.subnets
  vpc_id   = aws_vpc.sandbox.id
  # Only the automatic VPC-local route and the S3 gateway endpoint route exist.
  tags = merge(local.tags, { Name = "${var.name}-sandbox-${each.key}" })
}

resource "aws_route_table_association" "sandbox" {
  for_each       = var.subnets
  subnet_id      = aws_subnet.sandbox[each.key].id
  route_table_id = aws_route_table.sandbox[each.key].id
}

resource "aws_vpc_endpoint" "sandbox_s3" {
  vpc_id            = aws_vpc.sandbox.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [for route_table in aws_route_table.sandbox : route_table.id]
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "ReadPresignedInputs"
        Effect    = "Allow"
        Principal = "*"
        Action    = ["s3:GetObject"]
        Resource  = ["${var.artifact_bucket_arn}/sandbox/in/*"]
      },
      {
        Sid       = "WritePresignedOutputs"
        Effect    = "Allow"
        Principal = "*"
        Action    = ["s3:PutObject"]
        Resource  = ["${var.artifact_bucket_arn}/sandbox/out/*"]
      }
    ]
  })
  tags = merge(local.tags, { Name = "${var.name}-sandbox-s3" })
}

resource "aws_security_group" "sandbox" {
  name_prefix = "${var.name}-sandbox-"
  description = "Ephemeral guest: no inbound traffic, S3 gateway HTTPS only"
  vpc_id      = aws_vpc.sandbox.id
  ingress     = []
  egress {
    description     = "Only regional S3 gateway prefix list over HTTPS"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    prefix_list_ids = [data.aws_prefix_list.s3.id]
  }
  tags = local.tags
}
