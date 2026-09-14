terraform {
  required_version = ">= 1.0.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

variable "aws_profile" {
  type        = string
  default     = ""
  description = "AWS CLI profile to use for authentication (optional)"
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile != "" ? var.aws_profile : null
}

# ---------------------------------------------------------
# Variables
# ---------------------------------------------------------
variable "aws_region" {
  type        = string
  default     = "us-east-1"
  description = "AWS Region for deployment"
}

variable "bedrock_model_id" {
  type        = string
  default     = "anthropic.claude-3-5-sonnet-20241022-v2:0"
  description = "Amazon Bedrock model ID for Strands Agent"
}

variable "telegram_bot_token" {
  type        = string
  sensitive   = true
  default     = ""
  description = "Telegram Bot Token"
}

variable "parent_email" {
  type        = string
  default     = ""
  description = "Parent email for calendar notifications"
}

# ---------------------------------------------------------
# 1. AWS SQS Queues
# ---------------------------------------------------------
resource "aws_sqs_queue" "school_messages_dlq" {
  name                      = "parent-copilot-messages-dlq"
  message_retention_seconds = 1209600 # 14 days
}

resource "aws_sqs_queue" "school_messages_queue" {
  name                       = "parent-copilot-messages-queue"
  visibility_timeout_seconds = 90
  message_retention_seconds  = 86400 # 1 day

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.school_messages_dlq.arn
    maxReceiveCount     = 5
  })
}

# ---------------------------------------------------------
# 2. Amazon DynamoDB Table
# ---------------------------------------------------------
resource "aws_dynamodb_table" "parent_copilot_state" {
  name         = "ParentCopilotState"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  tags = {
    Environment = "production"
    Project     = "ParentCopilot"
  }
}

# ---------------------------------------------------------
# 3. AWS Secrets Manager
# ---------------------------------------------------------
resource "aws_secretsmanager_secret" "parent_copilot_secrets" {
  name                    = "parent-copilot/config"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "secrets_val" {
  secret_id = aws_secretsmanager_secret.parent_copilot_secrets.id
  secret_string = jsonencode({
    TELEGRAM_BOT_TOKEN = var.telegram_bot_token
    PARENT_EMAIL       = var.parent_email
  })
}

# ---------------------------------------------------------
# 4. IAM Role for Agent Worker (Bedrock + SQS + DynamoDB)
# ---------------------------------------------------------
resource "aws_iam_role" "agent_worker_role" {
  name = "parent-copilot-agent-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = ["lambda.amazonaws.com", "tasks.apprunner.amazonaws.com", "ecs-tasks.amazonaws.com"]
        }
      }
    ]
  })
}

resource "aws_iam_policy" "agent_worker_policy" {
  name        = "parent-copilot-agent-policy"
  description = "Allows Agent worker access to Bedrock, SQS, DynamoDB, and Secrets"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:SendMessage"
        ]
        Resource = aws_sqs_queue.school_messages_queue.arn
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:UpdateItem"
        ]
        Resource = [
          aws_dynamodb_table.parent_copilot_state.arn,
          "${aws_dynamodb_table.parent_copilot_state.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        # Allow reading the bot config secret AND the Google Calendar SA key secret
        Resource = [
          aws_secretsmanager_secret.parent_copilot_secrets.arn,
          "arn:aws:secretsmanager:${var.aws_region}:*:secret:parent-copilot/google-calendar-sa*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "attach_agent_policy" {
  role       = aws_iam_role.agent_worker_role.name
  policy_arn = aws_iam_policy.agent_worker_policy.arn
}

# ---------------------------------------------------------
# Outputs
# ---------------------------------------------------------
output "sqs_queue_url" {
  value       = aws_sqs_queue.school_messages_queue.url
  description = "URL of the SQS message queue"
}

output "dynamodb_table_name" {
  value       = aws_dynamodb_table.parent_copilot_state.name
  description = "DynamoDB table name"
}

output "secrets_arn" {
  value       = aws_secretsmanager_secret.parent_copilot_secrets.arn
  description = "Secrets Manager ARN"
}

output "agent_role_arn" {
  value       = aws_iam_role.agent_worker_role.arn
  description = "IAM Role ARN for Agent Worker"
}
