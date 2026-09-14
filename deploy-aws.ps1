param(
    [string]$AwsProfile
)

# =============================================================================
# Parent Co-pilot: AWS Deployment Script (PowerShell)
# Run from root: d:\tech\repositories\parent-ops-gcp
# =============================================================================

if ($AwsProfile) {
    $env:AWS_PROFILE = $AwsProfile
    Write-Host "Using AWS Profile: $AwsProfile" -ForegroundColor Cyan
}

$AWS_REGION     = "us-east-1"
$PARENT_EMAIL   = "muralidharan.balanandan@gmail.com"
if (-not $env:TELEGRAM_BOT_TOKEN) {
    Write-Host "Error: TELEGRAM_BOT_TOKEN environment variable not set." -ForegroundColor Red
    exit 1
}
$BOT_TOKEN      = $env:TELEGRAM_BOT_TOKEN
$BEDROCK_MODEL  = "anthropic.claude-3-5-sonnet-20241022-v2:0"

Write-Host "`n=== STEP 1: Verifying AWS CLI & Authentication ===" -ForegroundColor Cyan
try {
    $identity = aws sts get-caller-identity | ConvertFrom-Json
    Write-Host "Authenticated as AWS Account: $($identity.Account) ($($identity.Arn))" -ForegroundColor Green
} catch {
    Write-Host "Error: AWS CLI not authenticated. Please run 'aws configure' or set AWS environment variables." -ForegroundColor Red
    exit 1
}

Write-Host "`n=== STEP 2: Running Terraform Infrastructure Setup (Base) ===" -ForegroundColor Cyan
Push-Location terraform
try {
    terraform init
    # First, apply just the ECR repositories
    terraform apply -target="aws_ecr_repository.agent_worker_repo" -target="aws_ecr_repository.ingestion_repo" -auto-approve `
        -var="aws_region=$AWS_REGION" `
        -var="aws_profile=$AwsProfile" `
        -var="bedrock_model_id=$BEDROCK_MODEL" `
        -var="telegram_bot_token=$BOT_TOKEN" `
        -var="parent_email=$PARENT_EMAIL"
    
    $AGENT_ECR_URL = (terraform output -raw agent_ecr_repository_url)
    $INGESTION_ECR_URL = (terraform output -raw ingestion_ecr_repository_url)
} finally {
    Pop-Location
}

Write-Host "`n=== STEP 3: Building and Pushing Docker Images ===" -ForegroundColor Cyan
try {
    $identity = aws sts get-caller-identity | ConvertFrom-Json
    $account = $identity.Account
    $ecrToken = aws ecr get-login-password --region $AWS_REGION
    docker login --username AWS --password $ecrToken "$account.dkr.ecr.$AWS_REGION.amazonaws.com"
    
    Write-Host "Building Agent Image..." -ForegroundColor Cyan
    docker build -t parent-copilot-agent services/agent
    docker tag parent-copilot-agent:latest "$AGENT_ECR_URL`:latest"
    docker push "$AGENT_ECR_URL`:latest"
    
    Write-Host "Building Ingestion Image..." -ForegroundColor Cyan
    docker build -t parent-copilot-ingestion services/ingestion
    docker tag parent-copilot-ingestion:latest "$INGESTION_ECR_URL`:latest"
    docker push "$INGESTION_ECR_URL`:latest"
} catch {
    Write-Host "Error pushing to ECR. Make sure Docker is running." -ForegroundColor Red
}

Write-Host "`n=== STEP 4: Running Terraform (Full) ===" -ForegroundColor Cyan
Push-Location terraform
try {
    terraform apply -auto-approve `
        -var="aws_region=$AWS_REGION" `
        -var="aws_profile=$AwsProfile" `
        -var="bedrock_model_id=$BEDROCK_MODEL" `
        -var="telegram_bot_token=$BOT_TOKEN" `
        -var="parent_email=$PARENT_EMAIL"

    $SQS_URL       = (terraform output -raw sqs_queue_url)
    $DYNAMO_TABLE  = (terraform output -raw dynamodb_table_name)
    $SECRETS_ARN   = (terraform output -raw secrets_arn)
    $AGENT_ROLE    = (terraform output -raw agent_role_arn)
    $WEBHOOK_URL   = (terraform output -raw ingestion_webhook_url)

    Write-Host "SQS Queue URL  : $SQS_URL" -ForegroundColor Green
    Write-Host "DynamoDB Table : $DYNAMO_TABLE" -ForegroundColor Green
    Write-Host "Ingestion URL  : $WEBHOOK_URL" -ForegroundColor Green
} finally {
    Pop-Location
}

Write-Host "`n=== STEP 5: Registering Telegram Webhook ===" -ForegroundColor Cyan
if (-not $env:INGESTION_ENDPOINT_URL -and $WEBHOOK_URL) {
    $env:INGESTION_ENDPOINT_URL = $WEBHOOK_URL
}

if ($env:INGESTION_ENDPOINT_URL) {
    $webhookUrl = "$env:INGESTION_ENDPOINT_URL/webhook"
    $telegramSetWebhook = "https://api.telegram.org/bot$BOT_TOKEN/setWebhook?url=$webhookUrl"
    $result = Invoke-RestMethod -Uri $telegramSetWebhook -Method Get
    Write-Host "Telegram Webhook Response: $($result | ConvertTo-Json)" -ForegroundColor Green
} else {
    Write-Host "No Ingestion Endpoint found to configure webhook." -ForegroundColor Yellow
}

Write-Host "`n=== AWS DEPLOYMENT SETUP COMPLETE ===" -ForegroundColor Green
Write-Host "Strands Agent Worker (ECS Fargate) : Running" -ForegroundColor White
Write-Host "Ingestion Service (ECS + CloudFront): $WEBHOOK_URL" -ForegroundColor White
Write-Host "Telegram Webhook                   : $env:INGESTION_ENDPOINT_URL/webhook" -ForegroundColor White
Write-Host "DynamoDB State Table               : $DYNAMO_TABLE" -ForegroundColor White
Write-Host "SQS Message Queue  : $SQS_URL" -ForegroundColor White
