# ---------------------------------------------------------
# Ingestion Service: ECS Fargate + Application Load Balancer
# ---------------------------------------------------------

resource "aws_cloudwatch_log_group" "ingestion_logs" {
  name              = "/ecs/parent-copilot-ingestion"
  retention_in_days = 7
}

# IAM Task Role: allows ingestion container to push to SQS
resource "aws_iam_role" "ingestion_task_role" {
  name = "parent-copilot-ingestion-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "ecs-tasks.amazonaws.com" }
      }
    ]
  })
}

resource "aws_iam_policy" "ingestion_task_policy" {
  name        = "parent-copilot-ingestion-task-policy"
  description = "Allow ingestion task to send messages to SQS"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.school_messages_queue.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ingestion_task_policy_att" {
  role       = aws_iam_role.ingestion_task_role.name
  policy_arn = aws_iam_policy.ingestion_task_policy.arn
}

# Security Groups
resource "aws_security_group" "alb_sg" {
  name        = "parent-copilot-alb-sg"
  description = "Allow HTTP/HTTPS inbound for the ALB"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "ingestion_task_sg" {
  name        = "parent-copilot-ingestion-task-sg"
  description = "Allow traffic from ALB to ingestion task"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.alb_sg.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Application Load Balancer
resource "aws_lb" "ingestion_alb" {
  name               = "parent-copilot-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb_sg.id]
  subnets            = data.aws_subnets.default.ids
}

resource "aws_lb_target_group" "ingestion_tg" {
  name        = "parent-copilot-ingestion-tg"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = data.aws_vpc.default.id
  target_type = "ip"

  health_check {
    path                = "/"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 30
    timeout             = 5
  }
}

resource "aws_lb_listener" "ingestion_listener" {
  load_balancer_arn = aws_lb.ingestion_alb.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ingestion_tg.arn
  }
}

# ECS Task Definition for Ingestion Service
resource "aws_ecs_task_definition" "ingestion_task" {
  family                   = "parent-copilot-ingestion-task"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ingestion_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "ingestion"
      image     = "${aws_ecr_repository.ingestion_repo.repository_url}:latest"
      essential = true

      portMappings = [
        { containerPort = 8080, protocol = "tcp" }
      ]

      environment = [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "SQS_QUEUE_URL", value = aws_sqs_queue.school_messages_queue.url }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ingestion_logs.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])
}

# ECS Service for Ingestion
resource "aws_ecs_service" "ingestion_service" {
  name            = "parent-copilot-ingestion-service"
  cluster         = aws_ecs_cluster.parent_copilot_cluster.id
  task_definition = aws_ecs_task_definition.ingestion_task.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.ingestion_task_sg.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.ingestion_tg.arn
    container_name   = "ingestion"
    container_port   = 8080
  }

  depends_on = [aws_lb_listener.ingestion_listener]
}

