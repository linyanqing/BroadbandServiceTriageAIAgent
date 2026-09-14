variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "github_repo" {
  description = "GitHub repo this role trusts, as \"owner/name\" (e.g. \"linyanqing/BroadbandServiceTriageAIAgent\")."
  type        = string
}

variable "github_deploy_branch" {
  description = "Only pushes to this branch may assume the deploy role."
  type        = string
  default     = "main"
}

variable "create_oidc_provider" {
  description = "Set false if a GitHub OIDC provider already exists in this account (from another repo's CI/CD) -- AWS allows only one per issuer URL."
  type        = bool
  default     = true
}

variable "ecr_repository_arn" {
  type = string
}

variable "ecs_service_arn" {
  type = string
}

variable "ecs_cluster_arn" {
  type = string
}
