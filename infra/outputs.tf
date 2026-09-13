output "alb_dns_name" {
  description = "Public URL base for the API (http://<this>/api/v1/triage)"
  value       = module.alb.alb_dns_name
}

output "ecr_repository_url" {
  description = "Push the agent image here before the first full apply (see docs/deployment.md)"
  value       = module.ecr.repository_url
}

output "ecs_cluster_name" {
  value = module.ecs_fargate.cluster_name
}

output "ecs_service_name" {
  value = module.ecs_fargate.service_name
}

output "log_group_name" {
  value = module.ecs_fargate.log_group_name
}
