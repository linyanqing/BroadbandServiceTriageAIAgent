output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "cluster_arn" {
  value = aws_ecs_cluster.this.arn
}

output "service_name" {
  value = aws_ecs_service.agent.name
}

output "service_arn" {
  value = aws_ecs_service.agent.id
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.agent.name
}
