# Placeholders only -- Terraform creates the Secrets Manager entries but
# never writes their values. Populate the actual secret values out-of-band
# (console, `aws secretsmanager put-secret-value`, or a separate secure
# pipeline) so no credential ever appears in this repo or state file diffs.

resource "aws_secretsmanager_secret" "langchain_api_key" {
  name                    = "${var.project_name}/${var.environment}/langchain-api-key"
  description             = "LangSmith API key. Value must be set out-of-band; never via Terraform."
  recovery_window_in_days = 7
}
