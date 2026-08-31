variable "project_name" {
  description = "Short name used to prefix all created resources."
  type        = string
  default     = "broadband-triage-agent"
}

variable "environment" {
  description = "Deployment environment name (dev, staging, prod)."
  type        = string
  default     = "dev"
}

variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "ap-southeast-2"
}

variable "vpc_id" {
  description = "VPC to deploy the ALB and ECS service into."
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnets for the ALB."
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "Private subnets for the ECS Fargate tasks."
  type        = list(string)
}

variable "container_image" {
  description = "Fully qualified ECR image URI for the agent container (see docker/Dockerfile)."
  type        = string
}

variable "adot_image" {
  description = "AWS Distro for OpenTelemetry Collector image."
  type        = string
  default     = "public.ecr.aws/aws-observability/aws-otel-collector:v0.40.0"
}

variable "container_port" {
  type    = number
  default = 8000
}

variable "task_cpu" {
  type    = number
  default = 512
}

variable "task_memory" {
  type    = number
  default = 1024
}

variable "desired_count" {
  type    = number
  default = 1
}

variable "bedrock_model_id" {
  description = "Bedrock model ID with tool/function-calling support. Never hard-coded in application code -- set explicitly per environment."
  type        = string
}

variable "auto_approve_high_risk" {
  type    = bool
  default = false
}

variable "langsmith_tracing_enabled" {
  type    = bool
  default = false
}

variable "langsmith_project" {
  type    = string
  default = "broadband-triage-agent"
}

variable "cribl_otlp_endpoint" {
  description = "OTLP HTTP endpoint exposed by the enterprise Cribl instance that the ADOT Collector forwards enterprise telemetry to."
  type        = string
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "alarm_sns_topic_arn" {
  description = "Existing SNS topic ARN to notify on CloudWatch alarms. Leave empty to create alarms with no action."
  type        = string
  default     = ""
}
