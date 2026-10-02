output "queue_urls" {
  value = { for topic, queue in aws_sqs_queue.work : topic => queue.id }
}

output "queue_arns" {
  value = { for topic, queue in aws_sqs_queue.work : topic => queue.arn }
}

output "dead_letter_queue_arns" {
  value = { for topic, queue in aws_sqs_queue.dead_letter : topic => queue.arn }
}
