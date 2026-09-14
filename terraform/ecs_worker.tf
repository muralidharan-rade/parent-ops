data "aws_caller_identity" "current" {}

resource "aws_ecs_cluster" "parent_copilot_cluster" {
  name = "parent-copilot-cluster"
}

resource "aws_cloudwatch_log_group" "agent_worker_logs" {
  name              = "/ecs/parent-copilot-agent"
  retention_in_days = 7
}

resource "aws_iam_role" "ecs_task_execution_role" {
  name = "parent-copilot-ecs-execution-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_role_policy" {
  role       = aws_iam_role.ecs_task_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_ecs_task_definition" "agent_worker_task" {
  family                   = "parent-copilot-agent-task"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.agent_worker_role.arn

  container_definitions = jsonencode([
    {
      name      = "agent-worker"
      image     = "${aws_ecr_repository.agent_worker_repo.repository_url}:latest"
      essential = true
      
      environment = [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "SQS_QUEUE_URL", value = aws_sqs_queue.school_messages_queue.url },
        { name = "BEDROCK_MODEL_ID", value = var.bedrock_model_id },
        { name = "CALENDAR_SA_SECRET_NAME", value = "parent-copilot/google-calendar-sa" },
        { name = "PARENT_EMAIL", value = var.parent_email }
      ]
      secrets = [
        { name = "TELEGRAM_BOT_TOKEN", valueFrom = "${aws_secretsmanager_secret.parent_copilot_secrets.arn}:TELEGRAM_BOT_TOKEN::" }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.agent_worker_logs.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])
}

# Assume a default VPC exists to deploy the service
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_ecs_service" "agent_worker_service" {
  name            = "parent-copilot-agent-service"
  cluster         = aws_ecs_cluster.parent_copilot_cluster.id
  task_definition = aws_ecs_task_definition.agent_worker_task.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    assign_public_ip = true
  }
}
