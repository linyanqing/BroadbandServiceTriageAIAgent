module "ecr" {
  source = "./modules/ecr"

  project_name = var.project_name
}

module "secrets" {
  source = "./modules/secrets"

  project_name = var.project_name
  environment  = var.environment
}

module "iam" {
  source = "./modules/iam"

  project_name     = var.project_name
  environment      = var.environment
  aws_region       = var.aws_region
  bedrock_model_id = var.bedrock_model_id
  secret_arns      = [module.secrets.langchain_api_key_secret_arn]
}

module "alb" {
  source = "./modules/alb"

  project_name      = var.project_name
  environment       = var.environment
  vpc_id            = var.vpc_id
  public_subnet_ids = var.public_subnet_ids
  container_port    = var.container_port
}

module "ecs_fargate" {
  source = "./modules/ecs_fargate"

  project_name                 = var.project_name
  environment                  = var.environment
  aws_region                   = var.aws_region
  vpc_id                       = var.vpc_id
  private_subnet_ids           = var.private_subnet_ids
  alb_security_group_id        = module.alb.security_group_id
  target_group_arn             = module.alb.target_group_arn
  task_execution_role_arn      = module.iam.task_execution_role_arn
  task_role_arn                = module.iam.task_role_arn
  container_image              = var.container_image
  adot_image                   = var.adot_image
  container_port               = var.container_port
  task_cpu                     = var.task_cpu
  task_memory                  = var.task_memory
  desired_count                = var.desired_count
  assign_public_ip             = var.assign_public_ip
  bedrock_model_id             = var.bedrock_model_id
  auto_approve_high_risk       = var.auto_approve_high_risk
  langsmith_tracing_enabled    = var.langsmith_tracing_enabled
  langsmith_project            = var.langsmith_project
  langsmith_endpoint           = var.langsmith_endpoint
  langchain_api_key_secret_arn = module.secrets.langchain_api_key_secret_arn
  cribl_otlp_endpoint          = var.cribl_otlp_endpoint
  log_retention_days           = var.log_retention_days
}

module "cloudwatch" {
  source = "./modules/cloudwatch"

  project_name        = var.project_name
  environment         = var.environment
  alb_arn_suffix      = module.alb.alb_arn_suffix
  ecs_cluster_name    = module.ecs_fargate.cluster_name
  ecs_service_name    = module.ecs_fargate.service_name
  alarm_sns_topic_arn = var.alarm_sns_topic_arn
}
