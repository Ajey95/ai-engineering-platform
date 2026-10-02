locals {
  tags = merge(var.tags, { Application = "ai-engineering-platform" })
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "api" {
  name = "Managed-AllViewerExceptHostHeader"
}

resource "aws_s3_bucket" "web" {
  bucket        = "${var.name}-web"
  force_destroy = false
  tags          = local.tags
}

resource "aws_s3_bucket" "media" {
  bucket        = "${var.name}-private-media"
  force_destroy = false
  tags          = local.tags
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_public_access_block" "media" {
  bucket                  = aws_s3_bucket.media.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "web" {
  bucket = aws_s3_bucket.web.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_ownership_controls" "media" {
  bucket = aws_s3_bucket.media.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "web" {
  bucket = aws_s3_bucket.web.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "media" {
  bucket = aws_s3_bucket.media.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "media" {
  bucket = aws_s3_bucket.media.id
  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_cloudfront_origin_access_control" "s3" {
  name                              = "${var.name}-s3"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_public_key" "media" {
  name        = "${var.name}-media"
  encoded_key = file(var.cloudfront_public_key_pem_path)
  comment     = "Public key for short lived private recording cookies"
}

resource "aws_cloudfront_key_group" "media" {
  name    = "${var.name}-media"
  items   = [aws_cloudfront_public_key.media.id]
  comment = "Only signed recording paths may use the media origin"
}

resource "aws_cloudfront_distribution" "app" {
  enabled         = true
  is_ipv6_enabled = true
  aliases         = [var.app_domain]
  price_class     = var.price_class
  http_version    = "http2and3"
  comment         = "AI Engineering Platform application and private recordings"
  tags            = local.tags

  origin {
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_id                = "web"
    origin_access_control_id = aws_cloudfront_origin_access_control.s3.id
  }

  origin {
    domain_name              = aws_s3_bucket.media.bucket_regional_domain_name
    origin_id                = "media"
    origin_access_control_id = aws_cloudfront_origin_access_control.s3.id
  }

  origin {
    domain_name = var.api_origin_domain
    origin_id   = "api"
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_root_object = "index.html"
  default_cache_behavior {
    target_origin_id       = "web"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.disabled.id
    compress               = true
  }

  ordered_cache_behavior {
    path_pattern             = "/v1/*"
    target_origin_id         = "api"
    viewer_protocol_policy   = "https-only"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "PATCH", "POST", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.api.id
    compress                 = true
  }

  ordered_cache_behavior {
    path_pattern           = "/private-media/*"
    target_origin_id       = "media"
    viewer_protocol_policy = "https-only"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.optimized.id
    trusted_key_groups     = [aws_cloudfront_key_group.media.id]
    compress               = false
  }

  ordered_cache_behavior {
    path_pattern           = "/assets/*"
    target_origin_id       = "web"
    viewer_protocol_policy = "https-only"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.optimized.id
    compress               = true
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    acm_certificate_arn      = var.cloudfront_certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }
}

data "aws_iam_policy_document" "web_read" {
  statement {
    sid       = "CloudFrontWebRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.web.arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.app.arn]
    }
  }
}

data "aws_iam_policy_document" "media_read" {
  statement {
    sid       = "CloudFrontMediaRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.media.arn}/private-media/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.app.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "web" {
  bucket     = aws_s3_bucket.web.id
  policy     = data.aws_iam_policy_document.web_read.json
  depends_on = [aws_s3_bucket_public_access_block.web]
}

resource "aws_s3_bucket_policy" "media" {
  bucket     = aws_s3_bucket.media.id
  policy     = data.aws_iam_policy_document.media_read.json
  depends_on = [aws_s3_bucket_public_access_block.media]
}

data "aws_iam_policy_document" "media_publisher" {
  statement {
    sid       = "WriteScopedRecordings"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.media.arn}/private-media/*"]
    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["AES256"]
    }
  }
  statement {
    sid       = "VerifyScopedRecordings"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.media.arn}/private-media/*"]
  }
}

resource "aws_iam_policy" "media_publisher" {
  name   = "${var.name}-media-publisher"
  policy = data.aws_iam_policy_document.media_publisher.json
  tags   = local.tags
}

data "aws_iam_policy_document" "media_deletion" {
  statement {
    sid       = "ListScopedRecordings"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.media.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["private-media/*"]
    }
  }
  statement {
    sid       = "DeleteScopedRecordings"
    actions   = ["s3:DeleteObject"]
    resources = ["${aws_s3_bucket.media.arn}/private-media/*"]
  }
  statement {
    sid       = "InvalidateDeletedRecordings"
    actions   = ["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"]
    resources = [aws_cloudfront_distribution.app.arn]
  }
}

resource "aws_iam_policy" "media_deletion" {
  name   = "${var.name}-media-deletion"
  policy = data.aws_iam_policy_document.media_deletion.json
  tags   = local.tags
}
