output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "service_name" {
  value = aws_ecs_service.agent.name
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.agent.name
}
