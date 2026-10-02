output "app_url" {
  value = "https://${var.app_domain}"
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.app.id
}

output "cloudfront_domain_name" {
  value = aws_cloudfront_distribution.app.domain_name
}

output "cloudfront_key_pair_id" {
  value = aws_cloudfront_public_key.media.id
}

output "private_media_bucket" {
  value = aws_s3_bucket.media.bucket
}

output "web_bucket" {
  value = aws_s3_bucket.web.bucket
}

output "media_publisher_policy_arn" {
  value = aws_iam_policy.media_publisher.arn
}

output "media_deletion_policy_arn" {
  value = aws_iam_policy.media_deletion.arn
}
