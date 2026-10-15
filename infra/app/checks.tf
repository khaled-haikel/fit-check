# Asynchronous analyses (docs/adr/0006-async-analysis.md): POST /checks stores
# the job description and queues its ID; the same Lambda consumes the queue
# through the live alias and keeps progress, results and the spending counters
# in one table.

# On demand, no point-in-time recovery and no index: the items are 30-day
# results and counters that can be rebuilt.
resource "aws_dynamodb_table" "checks" {
  name         = local.checks_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

# Messages that failed twice land here and raise the alarm below.
resource "aws_sqs_queue" "checks_dlq" {
  name                      = local.checks_dlq_name
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "checks" {
  name = local.checks_name
  # Six times the function timeout, as AWS recommends for a Lambda consumer.
  visibility_timeout_seconds = 6 * local.lambda_timeout

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.checks_dlq.arn
    maxReceiveCount     = 2
  })
}

# The live alias consumes the queue, so a rollback also rolls back the worker.
# Lambda checks the role's SQS permissions when it creates the mapping, so the
# policy must be in place first.
resource "aws_lambda_event_source_mapping" "checks" {
  event_source_arn = aws_sqs_queue.checks.arn
  function_name    = aws_lambda_alias.live.arn
  batch_size       = 1

  scaling_config {
    maximum_concurrency = 2
  }

  depends_on = [aws_iam_role_policy.lambda]
}

resource "aws_sns_topic" "alerts" {
  name = local.alerts_name
}

# Stays pending until the address confirms it from the email AWS sends.
resource "aws_sns_topic_subscription" "alert_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "checks_dlq" {
  alarm_name          = "${local.checks_dlq_name}-messages"
  alarm_description   = "An analysis failed twice and its message is in the dead-letter queue (docs/runbook.md)."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.checks_dlq.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
