resource "aws_ecs_cluster" "this" {
  name = "${var.project_name}-${var.environment}"
}

resource "aws_cloudwatch_log_group" "agent" {
  name              = "/ecs/${var.project_name}-${var.environment}"
  retention_in_days = var.log_retention_days
}

resource "aws_security_group" "tasks" {
  name        = "${var.project_name}-${var.environment}-tasks"
  description = "Allow inbound from the ALB to the agent container"
  vpc_id      = var.vpc_id

  ingress {
    description     = "From ALB"
    from_port       = var.container_port
    to_port         = var.container_port
    protocol        = "tcp"
    security_groups = [var.alb_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

locals {
  adot_config = templatefile("${path.module}/templates/adot-config.yaml.tpl", {
    cribl_otlp_endpoint = var.cribl_otlp_endpoint
  })

  # LangSmith is a multi-region SaaS -- a key issued under an org hosted on
  # e.g. the APAC deployment authenticates ONLY against that region's API
  # host (403 Forbidden everywhere else, including the default US host,
  # discovered the hard way against a live deployment). Only add the
  # override when set, so accounts on the default (US) deployment are
  # unaffected -- LANGCHAIN_ENDPOINT="" would otherwise override the SDK's
  # own default with an empty string and break tracing entirely.
  langsmith_endpoint_env = var.langsmith_endpoint != "" ? [
    { name = "LANGCHAIN_ENDPOINT", value = var.langsmith_endpoint },
  ] : []
}

resource "aws_ecs_task_definition" "agent" {
  family                   = "${var.project_name}-${var.environment}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = var.task_execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "agent"
      image     = var.container_image
      essential = true
      portMappings = [
        { containerPort = var.container_port, protocol = "tcp" }
      ]
      environment = concat([
        { name = "ENVIRONMENT", value = var.environment },
        { name = "MOCK_MODE", value = "false" },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "BEDROCK_MODEL_ID", value = var.bedrock_model_id },
        { name = "AUTO_APPROVE_HIGH_RISK", value = tostring(var.auto_approve_high_risk) },
        { name = "OTEL_ENABLED", value = "true" },
        { name = "OTEL_EXPORTER_OTLP_ENDPOINT", value = "http://localhost:4318" },
        { name = "LANGCHAIN_TRACING_V2", value = tostring(var.langsmith_tracing_enabled) },
        { name = "LANGCHAIN_PROJECT", value = var.langsmith_project },
      ], local.langsmith_endpoint_env)
      secrets = [
        { name = "LANGCHAIN_API_KEY", valueFrom = var.langchain_api_key_secret_arn },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.agent.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "agent"
        }
      }
    },
    {
      name      = "adot-collector"
      image     = var.adot_image
      essential = true
      environment = [
        { name = "AOT_CONFIG_CONTENT", value = local.adot_config },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.agent.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "adot"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "agent" {
  name            = "${var.project_name}-${var.environment}"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.agent.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = var.assign_public_ip
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "agent"
    container_port   = var.container_port
  }
}
