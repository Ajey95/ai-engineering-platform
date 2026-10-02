locals {
  tags = merge(var.tags, { Application = "ai-engineering-platform", Boundary = "trusted" })
}

resource "aws_vpc" "trusted" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.tags, { Name = "${var.name}-trusted" })
}

resource "aws_internet_gateway" "trusted" {
  vpc_id = aws_vpc.trusted.id
  tags   = local.tags
}

resource "aws_subnet" "public" {
  for_each                = var.zones
  vpc_id                  = aws_vpc.trusted.id
  availability_zone       = each.key
  cidr_block              = each.value.public_cidr
  map_public_ip_on_launch = false
  tags                    = merge(local.tags, { Name = "${var.name}-public-${each.key}" })
}

resource "aws_subnet" "private" {
  for_each                = var.zones
  vpc_id                  = aws_vpc.trusted.id
  availability_zone       = each.key
  cidr_block              = each.value.private_cidr
  map_public_ip_on_launch = false
  tags                    = merge(local.tags, { Name = "${var.name}-private-${each.key}" })
}

resource "aws_eip" "nat" {
  for_each = var.zones
  domain   = "vpc"
  tags     = merge(local.tags, { Name = "${var.name}-nat-${each.key}" })
}

resource "aws_nat_gateway" "zone" {
  for_each      = var.zones
  allocation_id = aws_eip.nat[each.key].id
  subnet_id     = aws_subnet.public[each.key].id
  depends_on    = [aws_internet_gateway.trusted]
  tags          = merge(local.tags, { Name = "${var.name}-nat-${each.key}" })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.trusted.id
  tags   = merge(local.tags, { Name = "${var.name}-public" })
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.trusted.id
}

resource "aws_route_table_association" "public" {
  for_each       = var.zones
  subnet_id      = aws_subnet.public[each.key].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  for_each = var.zones
  vpc_id   = aws_vpc.trusted.id
  tags     = merge(local.tags, { Name = "${var.name}-private-${each.key}" })
}

resource "aws_route" "private_egress" {
  for_each               = var.zones
  route_table_id         = aws_route_table.private[each.key].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.zone[each.key].id
}

resource "aws_route_table_association" "private" {
  for_each       = var.zones
  subnet_id      = aws_subnet.private[each.key].id
  route_table_id = aws_route_table.private[each.key].id
}

resource "aws_cloudwatch_log_group" "flow" {
  name              = "/aip/${var.name}/trusted-vpc-flow"
  retention_in_days = 30
  tags              = local.tags
}

data "aws_iam_policy_document" "flow_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "flow" {
  name               = "${var.name}-trusted-flow"
  assume_role_policy = data.aws_iam_policy_document.flow_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "flow_write" {
  statement {
    actions = [
      "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
    ]
    resources = ["${aws_cloudwatch_log_group.flow.arn}:*"]
  }
}

resource "aws_iam_role_policy" "flow" {
  name   = "flow-logs"
  role   = aws_iam_role.flow.id
  policy = data.aws_iam_policy_document.flow_write.json
}

resource "aws_flow_log" "trusted" {
  iam_role_arn    = aws_iam_role.flow.arn
  log_destination = aws_cloudwatch_log_group.flow.arn
  traffic_type    = "ALL"
  vpc_id          = aws_vpc.trusted.id
  tags            = local.tags
}
