output "high_5xx_alarm_arn" {
  value = aws_cloudwatch_metric_alarm.high_5xx.arn
}

output "high_cpu_alarm_arn" {
  value = aws_cloudwatch_metric_alarm.high_cpu.arn
}
