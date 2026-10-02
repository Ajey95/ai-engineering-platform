output "app_url" {
  value = module.edge.app_url
}

output "api_origin_dns_name" {
  value = module.control_plane.api_alb_dns_name
}

output "postgres_endpoint" {
  value = module.control_plane.postgres_endpoint
}

output "postgres_master_secret_arn" {
  value     = module.control_plane.postgres_master_secret_arn
  sensitive = true
}

output "ecs_cluster_arn" {
  value = module.control_plane.ecs_cluster_arn
}

output "agent_queue_url" {
  value = module.queues.queue_urls["agent"]
}

output "web_bucket" {
  value = module.edge.web_bucket
}

output "private_media_bucket" {
  value = module.edge.private_media_bucket
}

output "sandbox_artifact_bucket" {
  value = module.sandbox_artifacts.bucket_name
}
