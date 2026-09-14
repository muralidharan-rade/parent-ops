resource "aws_ecr_repository" "agent_worker_repo" {
  name                 = "parent-copilot-agent"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "ingestion_repo" {
  name                 = "parent-copilot-ingestion"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

output "agent_ecr_repository_url" {
  value       = aws_ecr_repository.agent_worker_repo.repository_url
  description = "ECR Repository URL for the agent worker"
}

output "ingestion_ecr_repository_url" {
  value       = aws_ecr_repository.ingestion_repo.repository_url
  description = "ECR Repository URL for the ingestion webhook"
}
