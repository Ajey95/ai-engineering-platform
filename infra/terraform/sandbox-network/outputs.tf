output "vpc_id" {
  value = aws_vpc.sandbox.id
}

output "private_subnet_ids_by_az" {
  value = { for az, subnet in aws_subnet.sandbox : az => subnet.id }
}

output "sandbox_security_group_id" {
  value = aws_security_group.sandbox.id
}

output "s3_endpoint_id" {
  value = aws_vpc_endpoint.sandbox_s3.id
}
