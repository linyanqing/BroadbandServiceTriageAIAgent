# Lets GitHub Actions deploy this app WITHOUT any long-lived AWS access
# keys stored as GitHub secrets -- it authenticates via OIDC (a short-lived
# token GitHub itself issues per workflow run), which this role's trust
# policy accepts only from this exact repo, and only on pushes to `main`
# (a PR from any other branch, or from a fork, cannot assume this role).
#
# The role's own permissions are equally narrow: push access to only this
# one ECR repository, and update access to only this one ECS service --
# nothing else in the account. Deliberately does NOT include
# ecs:RegisterTaskDefinition, terraform state access, or any IAM/VPC/ALB
# permissions: the CD pipeline only ever builds an image, pushes it, and
# force-redeploys the existing task definition (the same three commands
# used to deploy this app manually) -- it never runs `terraform apply`,
# since this repo's Terraform state is local-only (infra/terraform.tfstate,
# gitignored), not something a CI runner has access to.

data "aws_caller_identity" "current" {}

# Reuse an existing GitHub OIDC provider if one is already registered in
# this account (e.g. from another repo's CI/CD) instead of failing to
# create a duplicate -- AWS accounts can only have one per issuer URL.
data "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 0 : 1
  url   = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # GitHub's own OIDC signing certificate thumbprint (well-known, documented
  # by GitHub and AWS) -- not a secret, just how AWS validates the
  # provider's TLS cert chain over the OIDC discovery endpoint.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1", "1c58a3a8518e8759bf075b76b750d4f2df264fcd"]
}

locals {
  oidc_provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      # StringLike (not StringEquals): GitHub's `sub` claim now appends an
      # immutable numeric ID to both the owner and repo name segments --
      # confirmed via CloudTrail on the first real deploy attempt, e.g.
      # "repo:linyanqing@6722774/BroadbandServiceTriageAIAgent@1352340657:
      # ref:refs/heads/main", not the plain "owner/repo" form docs/examples
      # commonly show. The wildcards below only ever match that numeric
      # suffix -- the literal "linyanqing@"/"BroadbandServiceTriageAIAgent@"
      # prefixes still have to match exactly, so this is no looser a trust
      # boundary than an exact match, just robust to the ID (which doesn't
      # change for this repo, but isn't worth hardcoding as an opaque
      # magic number in this file).
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${split("/", var.github_repo)[0]}@*/${split("/", var.github_repo)[1]}@*:ref:refs/heads/${var.github_deploy_branch}"
      ]
    }
  }
}

resource "aws_iam_role" "github_actions_deploy" {
  name               = "${var.project_name}-${var.environment}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.trust.json
}

data "aws_iam_policy_document" "deploy_permissions" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # this specific action has no resource-level permissions in AWS
  }

  statement {
    sid = "EcrPushToThisRepoOnly"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:PutImage",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
    ]
    resources = [var.ecr_repository_arn]
  }

  statement {
    sid       = "EcsRedeployThisServiceOnly"
    actions   = ["ecs:UpdateService", "ecs:DescribeServices"]
    resources = [var.ecs_service_arn]
  }

  statement {
    sid       = "EcsInspectTasksForDeployHealthCheck"
    actions   = ["ecs:DescribeTasks", "ecs:ListTasks"]
    resources = ["*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [var.ecs_cluster_arn]
    }
  }
}

resource "aws_iam_role_policy" "deploy_permissions" {
  name   = "deploy-this-app-only"
  role   = aws_iam_role.github_actions_deploy.id
  policy = data.aws_iam_policy_document.deploy_permissions.json
}
