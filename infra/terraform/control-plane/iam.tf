data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  for_each           = local.workloads
  name               = "${var.name}-${each.key}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "execution" {
  for_each   = local.workloads
  role       = aws_iam_role.execution[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  for_each = local.workloads
  statement {
    sid       = "ReadInjectedSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = values(var.secret_environment[each.key])
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  for_each = local.workloads
  name     = "injected-secrets"
  role     = aws_iam_role.execution[each.key].id
  policy   = data.aws_iam_policy_document.execution_secrets[each.key].json
}

resource "aws_iam_role" "workload" {
  for_each           = local.workloads
  name               = "${var.name}-${each.key}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "efs" {
  statement {
    sid       = "MountOnlyPrivateArtifactAccessPoint"
    actions   = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
    resources = [aws_efs_file_system.artifacts.arn]
    condition {
      test     = "StringEquals"
      variable = "elasticfilesystem:AccessPointArn"
      values   = [aws_efs_access_point.artifacts.arn]
    }
  }
}

resource "aws_iam_role_policy" "efs" {
  for_each = local.artifact_workloads
  name     = "private-artifacts"
  role     = aws_iam_role.workload[each.key].id
  policy   = data.aws_iam_policy_document.efs.json
}

data "aws_iam_policy_document" "api" {
  statement {
    sid       = "ReadScopedGuestEvidence"
    actions   = ["s3:GetObject"]
    resources = ["${var.sandbox_artifact_bucket_arn}/sandbox/out/*"]
  }
  statement {
    sid       = "ReadPrivateRecordingObjects"
    actions   = ["s3:GetObject"]
    resources = ["${var.private_media_bucket_arn}/*"]
  }
  statement {
    sid       = "ListPrivateRecordingObjects"
    actions   = ["s3:ListBucket"]
    resources = [var.private_media_bucket_arn]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "api-read"
  role   = aws_iam_role.workload["api"].id
  policy = data.aws_iam_policy_document.api.json
}

data "aws_iam_policy_document" "agent" {
  statement {
    sid       = "RunQueue"
    actions   = ["sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"]
    resources = [var.agent_queue_arn]
  }
  statement {
    sid       = "ScopedSandboxArtifacts"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${var.sandbox_artifact_bucket_arn}/sandbox/*"]
  }
  statement {
    sid       = "DescribeSandboxInstances"
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }
  statement {
    sid       = "LaunchBoundedSandbox"
    actions   = ["ec2:RunInstances"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:InstanceType"
      values   = [var.sandbox_instance_type]
    }
  }
  statement {
    sid     = "TagLaunchedSandbox"
    actions = ["ec2:CreateTags"]
    resources = [
      "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/*",
      "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:volume/*",
    ]
    condition {
      test     = "StringEquals"
      variable = "ec2:CreateAction"
      values   = ["RunInstances"]
    }
  }
  statement {
    sid       = "SealAndTerminateManagedSandbox"
    actions   = ["ec2:ModifyInstanceMetadataOptions", "ec2:TerminateInstances"]
    resources = ["arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/aip:managed"
      values   = ["sandbox"]
    }
  }
}

resource "aws_iam_role_policy" "agent" {
  name   = "hosted-sandbox"
  role   = aws_iam_role.workload["agent"].id
  policy = data.aws_iam_policy_document.agent.json
}

data "aws_iam_policy_document" "run_relay" {
  statement {
    sid       = "PublishRunWakeups"
    actions   = ["sqs:SendMessage"]
    resources = [var.agent_queue_arn]
  }
}

resource "aws_iam_role_policy" "run_relay" {
  name   = "run-queue-publisher"
  role   = aws_iam_role.workload["run_relay"].id
  policy = data.aws_iam_policy_document.run_relay.json
}

data "aws_iam_policy_document" "sandbox_cleanup" {
  statement {
    sid       = "InspectManagedSandbox"
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }
  statement {
    sid       = "TerminateManagedSandbox"
    actions   = ["ec2:TerminateInstances"]
    resources = ["arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/aip:managed"
      values   = ["sandbox"]
    }
  }
  statement {
    sid       = "FenceGuestOutputs"
    actions   = ["s3:PutObject"]
    resources = ["${var.sandbox_artifact_bucket_arn}/sandbox/out/*"]
  }
  statement {
    sid       = "RevokeGuestGoMarker"
    actions   = ["s3:DeleteObject"]
    resources = ["${var.sandbox_artifact_bucket_arn}/sandbox/in/*"]
  }
}

resource "aws_iam_role_policy" "sandbox_cleanup" {
  name   = "sandbox-cleanup"
  role   = aws_iam_role.workload["sandbox_cleanup"].id
  policy = data.aws_iam_policy_document.sandbox_cleanup.json
}

# GitHub publication uses an injected token. The trusted task role has no
# EC2/SQS/S3 authority; candidate bytes come from the private artifact volume.

data "aws_iam_policy_document" "media_queue" {
  statement {
    sid       = "MediaQueue"
    actions   = ["sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"]
    resources = [var.media_queue_arn]
  }
}

resource "aws_iam_role_policy" "media_queue" {
  name   = "media-queue"
  role   = aws_iam_role.workload["media"].id
  policy = data.aws_iam_policy_document.media_queue.json
}

resource "aws_iam_role_policy_attachment" "media_publisher" {
  role       = aws_iam_role.workload["media"].name
  policy_arn = var.media_publisher_policy_arn
}

resource "aws_iam_role_policy_attachment" "media_cleanup" {
  role       = aws_iam_role.workload["media_cleanup"].name
  policy_arn = var.media_deletion_policy_arn
}
