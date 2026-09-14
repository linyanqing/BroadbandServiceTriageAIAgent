# Least-privilege IAM: a task EXECUTION role (pull image, write logs, read
# secrets referenced by the task definition) separate from the task ROLE
# (what the running application code itself is allowed to call -- scoped to
# only the Bedrock model it was configured with).

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "ecs_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task_execution" {
  name               = "${var.project_name}-${var.environment}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

resource "aws_iam_role_policy_attachment" "task_execution_managed" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "task_execution_secrets" {
  statement {
    sid       = "ReadAgentSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = var.secret_arns
  }
}

resource "aws_iam_role_policy" "task_execution_secrets" {
  count  = length(var.secret_arns) > 0 ? 1 : 0
  name   = "read-secrets"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.task_execution_secrets.json
}

resource "aws_iam_role" "task" {
  name               = "${var.project_name}-${var.environment}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

# Cross-region inference profile IDs carry a geography prefix (us., eu.,
# apac., au., global., ...) that a bare foundation-model ID never does.
# Invoking a profile requires bedrock:InvokeModel on BOTH the profile ARN
# and every underlying foundation-model ARN it can route to (which can span
# multiple regions) -- granting only the foundation-model form here was a
# real bug caught in first live deployment (AccessDeniedException on
# bedrock:InvokeModel even though the model itself was invocable).
locals {
  bedrock_is_inference_profile = can(regex("^(us|eu|apac|au|global|jp|ca)\\.", var.bedrock_model_id))
}

data "aws_bedrock_inference_profile" "selected" {
  count                = local.bedrock_is_inference_profile ? 1 : 0
  inference_profile_id = var.bedrock_model_id
}

locals {
  bedrock_resources = local.bedrock_is_inference_profile ? concat(
    [data.aws_bedrock_inference_profile.selected[0].inference_profile_arn],
    [for m in data.aws_bedrock_inference_profile.selected[0].models : m.model_arn],
  ) : ["arn:aws:bedrock:${var.aws_region}::foundation-model/${var.bedrock_model_id}"]
}

data "aws_iam_policy_document" "task_bedrock" {
  statement {
    sid       = "InvokeConfiguredBedrockModel"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = local.bedrock_resources
  }
}

resource "aws_iam_role_policy" "task_bedrock" {
  name   = "invoke-bedrock-model"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task_bedrock.json
}
