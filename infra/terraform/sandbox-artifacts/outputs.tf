output "bucket_name" {
  value = aws_s3_bucket.sandbox.id
}

output "bucket_arn" {
  value = aws_s3_bucket.sandbox.arn
}
