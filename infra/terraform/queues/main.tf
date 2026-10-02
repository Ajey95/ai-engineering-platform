locals {
  tags = merge(var.tags, { Application = "ai-engineering-platform" })
  queues = {
    agent = {
      visibility = 120
      retention  = 345600
    }
    media = {
      visibility = 900
      retention  = 345600
    }
    projection = {
      visibility = 120
      retention  = 345600
    }
  }
}

resource "aws_sqs_queue" "dead_letter" {
  for_each                  = local.queues
  name                      = "${var.name}-${each.key}-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
  receive_wait_time_seconds = 20
  tags                      = merge(local.tags, { Queue = each.key, Purpose = "dead-letter" })
}

resource "aws_sqs_queue" "work" {
  for_each                   = local.queues
  name                       = "${var.name}-${each.key}"
  visibility_timeout_seconds = each.value.visibility
  message_retention_seconds  = each.value.retention
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letter[each.key].arn
    maxReceiveCount     = 5
  })
  tags = merge(local.tags, { Queue = each.key, Purpose = "work" })
}

resource "aws_sqs_queue_redrive_allow_policy" "dead_letter" {
  for_each  = local.queues
  queue_url = aws_sqs_queue.dead_letter[each.key].id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.work[each.key].arn]
  })
}
