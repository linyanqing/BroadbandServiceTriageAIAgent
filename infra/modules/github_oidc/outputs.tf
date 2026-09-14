output "deploy_role_arn" {
  description = "Paste this into .github/workflows/deploy.yml's role-to-assume."
  value       = aws_iam_role.github_actions_deploy.arn
}
