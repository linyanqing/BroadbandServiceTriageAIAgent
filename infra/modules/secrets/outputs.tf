output "langchain_api_key_secret_arn" {
  value = aws_secretsmanager_secret.langchain_api_key.arn
}
