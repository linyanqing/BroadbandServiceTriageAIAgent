variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "alb_security_group_id" {
  type = string
}

variable "target_group_arn" {
  type = string
}

variable "task_execution_role_arn" {
  type = string
}

variable "task_role_arn" {
  type = string
}

variable "container_image" {
  type = string
}

variable "adot_image" {
  type = string
}

variable "container_port" {
  type = number
}

variable "task_cpu" {
  type = number
}

variable "task_memory" {
  type = number
}

variable "desired_count" {
  type = number
}

variable "assign_public_ip" {
  description = "Assign a public IP to tasks -- required when private_subnet_ids has no NAT gateway (e.g. a default VPC's public subnets)."
  type        = bool
  default     = false
}

variable "bedrock_model_id" {
  type = string
}

variable "auto_approve_high_risk" {
  type = bool
}

variable "langsmith_tracing_enabled" {
  type = bool
}

variable "langsmith_project" {
  type = string
}

variable "langsmith_endpoint" {
  description = "Regional LangSmith API host (e.g. https://apac.api.smith.langchain.com). Leave empty for the SDK's default (US) host -- only accounts on a non-default regional deployment need this."
  type        = string
  default     = ""
}

variable "langchain_api_key_secret_arn" {
  type = string
}

variable "cribl_otlp_endpoint" {
  type = string
}

variable "log_retention_days" {
  type = number
}
