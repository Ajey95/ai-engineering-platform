output "vpc_id" {
  value = aws_vpc.trusted.id
}

output "public_subnet_ids" {
  value = [for zone in sort(keys(var.zones)) : aws_subnet.public[zone].id]
}

output "private_subnet_ids" {
  value = [for zone in sort(keys(var.zones)) : aws_subnet.private[zone].id]
}

output "nat_public_ips" {
  value = { for zone, eip in aws_eip.nat : zone => eip.public_ip }
}
