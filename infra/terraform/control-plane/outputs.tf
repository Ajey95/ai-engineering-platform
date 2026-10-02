output "api_alb_dns_name" {
  value = aws_lb.api.dns_name
}

output "api_alb_zone_id" {
  value = aws_lb.api.zone_id
}

output "postgres_endpoint" {
  value = aws_db_instance.postgres.endpoint
}

output "postgres_master_secret_arn" {
  value     = aws_db_instance.postgres.master_user_secret[0].secret_arn
  sensitive = true
}

output "artifact_file_system_id" {
  value = aws_efs_file_system.artifacts.id
}

output "artifact_access_point_id" {
  value = aws_efs_access_point.artifacts.id
}

output "ecs_cluster_arn" {
  value = aws_ecs_cluster.control.arn
}

output "ecs_service_names" {
  value = { for name, service in aws_ecs_service.control : name => service.name }
}
