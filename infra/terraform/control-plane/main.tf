locals {
  tags               = merge(var.tags, { Application = "ai-engineering-platform", Boundary = "trusted" })
  artifact_workloads = toset(["api", "agent", "publication", "media", "media_cleanup"])
  common_environment = {
    AIP_ENVIRONMENT                = "production"
    AIP_ARTIFACT_DIR               = "/var/lib/aip/artifacts"
    AIP_PUBLIC_BASE_URL            = var.public_base_url
    AIP_HOSTED_EXECUTION_ENABLED   = tostring(var.hosted_execution_enabled)
    AIP_RUN_QUEUE_URL              = var.agent_queue_url
    AIP_MEDIA_QUEUE_URL            = var.media_queue_url
    AIP_SANDBOX_ARTIFACT_BUCKET    = var.sandbox_artifact_bucket_name
    AIP_SANDBOX_AMI_ID             = var.sandbox_ami_id
    AIP_SANDBOX_SUBNET_ID          = var.sandbox_subnet_id
    AIP_SANDBOX_SECURITY_GROUP_ID  = var.sandbox_security_group_id
    AIP_SANDBOX_INSTANCE_TYPE      = var.sandbox_instance_type
    AIP_SANDBOX_ROOT_DEVICE_NAME   = var.sandbox_root_device_name
    AIP_PRIVATE_MEDIA_BUCKET       = var.private_media_bucket_name
    AIP_CLOUDFRONT_DISTRIBUTION_ID = var.cloudfront_distribution_id
    AIP_CLOUDFRONT_KEY_PAIR_ID     = var.cloudfront_key_pair_id
  }
  workloads = {
    api = {
      cpu     = 1024
      memory  = 2048
      desired = 2
      command = ["python", "-m", "uvicorn", "platform_app.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
    }
    agent = {
      cpu     = 2048
      memory  = 4096
      desired = 2
      command = ["python", "-m", "scripts.consume_hosted_dispatch", "--serve"]
    }
    run_relay = {
      cpu     = 512
      memory  = 1024
      desired = 1
      command = ["python", "-m", "scripts.relay_run_dispatch", "--serve"]
    }
    sandbox_cleanup = {
      cpu     = 512
      memory  = 1024
      desired = 1
      command = ["python", "-m", "scripts.dispatch_sandbox_cleanup", "--serve"]
    }
    graph_projection = {
      cpu     = 512
      memory  = 1024
      desired = 1
      command = ["python", "-m", "scripts.project_memory_graph", "--serve"]
    }
    operations = {
      cpu     = 512
      memory  = 1024
      desired = 1
      command = ["python", "-m", "scripts.evaluate_operations", "--serve"]
    }
    alert_delivery = {
      cpu     = 512
      memory  = 1024
      desired = 1
      command = ["python", "-m", "scripts.dispatch_alerts", "--serve"]
    }
    publication = {
      cpu     = 512
      memory  = 1024
      desired = 1
      command = ["python", "-m", "scripts.dispatch_publications", "--serve"]
    }
    media = {
      cpu     = 2048
      memory  = 4096
      desired = 2
      command = ["python", "-m", "scripts.dispatch_hosted_media", "--serve"]
    }
    media_cleanup = {
      cpu     = 512
      memory  = 1024
      desired = 1
      command = ["python", "-m", "scripts.dispatch_private_media_deletions", "--serve"]
    }
  }
}

resource "aws_security_group" "alb" {
  name_prefix = "${var.name}-alb-"
  description = "HTTPS API origin"
  vpc_id      = var.vpc_id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS clients and CloudFront"
  }
  egress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.tasks.id]
    description     = "API tasks only"
  }
  tags = local.tags
}

resource "aws_security_group" "tasks" {
  name_prefix = "${var.name}-tasks-"
  description = "Trusted API and control workers"
  vpc_id      = var.vpc_id
  ingress     = []
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Controlled NAT to providers, GitHub, AWS APIs and OIDC"
  }
  tags = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "api_from_alb" {
  security_group_id            = aws_security_group.tasks.id
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
  description                  = "ALB to API"
}

resource "aws_security_group" "database" {
  name_prefix = "${var.name}-db-"
  description = "PostgreSQL from trusted tasks"
  vpc_id      = var.vpc_id
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.tasks.id]
  }
  egress = []
  tags   = local.tags
}

resource "aws_security_group" "artifacts" {
  name_prefix = "${var.name}-efs-"
  description = "Private artifact volume from trusted tasks"
  vpc_id      = var.vpc_id
  ingress {
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.tasks.id]
  }
  egress = []
  tags   = local.tags
}

resource "aws_lb" "api" {
  name                       = substr("${var.name}-api", 0, 32)
  internal                   = false
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = var.public_subnet_ids
  enable_deletion_protection = true
  drop_invalid_header_fields = true
  tags                       = local.tags
}

resource "aws_lb_target_group" "api" {
  name        = substr("${var.name}-api", 0, 32)
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id
  health_check {
    path                = "/v1/ready"
    matcher             = "200"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
  deregistration_delay = 30
  tags                 = local.tags
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.api.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.api_certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
  tags = local.tags
}

resource "aws_db_subnet_group" "postgres" {
  name       = "${var.name}-postgres"
  subnet_ids = var.private_subnet_ids
  tags       = local.tags
}

resource "aws_db_instance" "postgres" {
  identifier                   = "${var.name}-postgres"
  engine                       = "postgres"
  instance_class               = var.db_instance_class
  db_name                      = "aip"
  username                     = "aip_admin"
  manage_master_user_password  = true
  allocated_storage            = var.db_allocated_storage_gib
  max_allocated_storage        = var.db_allocated_storage_gib * 2
  storage_type                 = "gp3"
  storage_encrypted            = true
  multi_az                     = true
  publicly_accessible          = false
  db_subnet_group_name         = aws_db_subnet_group.postgres.name
  vpc_security_group_ids       = [aws_security_group.database.id]
  backup_retention_period      = 7
  backup_window                = "03:00-04:00"
  maintenance_window           = "sun:04:00-sun:05:00"
  deletion_protection          = true
  skip_final_snapshot          = false
  final_snapshot_identifier    = "${var.name}-postgres-final"
  auto_minor_version_upgrade   = true
  copy_tags_to_snapshot        = true
  performance_insights_enabled = true
  apply_immediately            = false
  tags                         = local.tags
}

resource "aws_efs_file_system" "artifacts" {
  creation_token   = "${var.name}-trusted-artifacts"
  encrypted        = true
  performance_mode = "generalPurpose"
  throughput_mode  = "elastic"
  tags             = merge(local.tags, { Name = "${var.name}-trusted-artifacts" })
}

resource "aws_efs_access_point" "artifacts" {
  file_system_id = aws_efs_file_system.artifacts.id
  posix_user {
    uid = 10001
    gid = 10001
  }
  root_directory {
    path = "/artifacts"
    creation_info {
      owner_uid   = 10001
      owner_gid   = 10001
      permissions = "0700"
    }
  }
  tags = local.tags
}

resource "aws_efs_mount_target" "artifacts" {
  for_each        = toset(var.private_subnet_ids)
  file_system_id  = aws_efs_file_system.artifacts.id
  subnet_id       = each.value
  security_groups = [aws_security_group.artifacts.id]
}

resource "aws_cloudwatch_log_group" "tasks" {
  for_each          = local.workloads
  name              = "/aip/${var.name}/${each.key}"
  retention_in_days = 30
  tags              = local.tags
}

resource "aws_ecs_cluster" "control" {
  name = "${var.name}-control"
  setting {
    name  = "containerInsights"
    value = "enhanced"
  }
  tags = local.tags
}

resource "aws_ecs_task_definition" "control" {
  for_each                 = local.workloads
  family                   = "${var.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(each.value.cpu)
  memory                   = tostring(each.value.memory)
  execution_role_arn       = aws_iam_role.execution[each.key].arn
  task_role_arn            = aws_iam_role.workload[each.key].arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  dynamic "volume" {
    for_each = contains(local.artifact_workloads, each.key) ? [1] : []
    content {
      name = "artifacts"
      efs_volume_configuration {
        file_system_id     = aws_efs_file_system.artifacts.id
        transit_encryption = "ENABLED"
        authorization_config {
          access_point_id = aws_efs_access_point.artifacts.id
          iam             = "ENABLED"
        }
      }
    }
  }
  container_definitions = jsonencode([{
    name                   = each.key
    image                  = contains(["media", "media_cleanup"], each.key) ? var.media_image_uri : var.image_uri
    essential              = true
    command                = each.value.command
    readonlyRootFilesystem = true
    user                   = "10001:10001"
    mountPoints = contains(local.artifact_workloads, each.key) ? [{
      sourceVolume  = "artifacts"
      containerPath = "/var/lib/aip/artifacts"
      readOnly      = false
    }] : []
    portMappings = each.key == "api" ? [{
      containerPort = 8000
      hostPort      = 8000
      protocol      = "tcp"
    }] : []
    environment = [for name, value in local.common_environment : {
      name  = name
      value = value
    }]
    secrets = [for name, arn in var.secret_environment[each.key] : {
      name      = name
      valueFrom = arn
    }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.tasks[each.key].name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = each.key
      }
    }
  }])
  tags = local.tags
}

resource "aws_ecs_service" "control" {
  for_each                           = local.workloads
  name                               = "${var.name}-${each.key}"
  cluster                            = aws_ecs_cluster.control.id
  task_definition                    = aws_ecs_task_definition.control[each.key].arn
  desired_count                      = var.deploy_services ? each.value.desired : 0
  launch_type                        = "FARGATE"
  platform_version                   = "1.4.0"
  deployment_minimum_healthy_percent = 50
  deployment_maximum_percent         = 200
  enable_execute_command             = false
  health_check_grace_period_seconds  = each.key == "api" ? 60 : null
  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }
  dynamic "load_balancer" {
    for_each = each.key == "api" ? [1] : []
    content {
      target_group_arn = aws_lb_target_group.api.arn
      container_name   = "api"
      container_port   = 8000
    }
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  depends_on = [aws_lb_listener.https, aws_efs_mount_target.artifacts]
  tags       = local.tags
}
