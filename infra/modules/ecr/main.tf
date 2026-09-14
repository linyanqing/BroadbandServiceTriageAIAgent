# ECR repository for the agent container image. Created ahead of the rest of
# the stack so `docker push` has somewhere to go before the ECS service (which
# references the image by tag) is created -- see docs/deployment.md for the
# two-phase bootstrap (`-target=module.ecr` first, then a full apply).

resource "aws_ecr_repository" "agent" {
  name                 = var.project_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "agent" {
  repository = aws_ecr_repository.agent.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 10 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}
