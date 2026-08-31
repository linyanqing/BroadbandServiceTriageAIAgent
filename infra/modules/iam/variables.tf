variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "bedrock_model_id" {
  type = string
}

variable "secret_arns" {
  description = "Secrets Manager ARNs the task execution role may resolve into environment variables."
  type        = list(string)
  default     = []
}
