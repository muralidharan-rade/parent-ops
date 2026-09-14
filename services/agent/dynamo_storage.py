import os
import time
import json
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "ParentCopilotState")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

_LOCAL_STORAGE: List[Dict[str, Any]] = []


def get_dynamodb_resource():
    """Returns a DynamoDB resource. Supports IAM Task Roles (ECS/Lambda), env credentials, and local profiles."""
    try:
        import boto3
        from botocore.exceptions import NoCredentialsError, NoRegionError
        resource = boto3.resource("dynamodb", region_name=AWS_REGION)
        # Probe credentials by calling STS — works with IAM roles, env vars, and profiles
        boto3.client("sts", region_name=AWS_REGION).get_caller_identity()
        return resource
    except Exception as e:
        logger.warning(f"AWS DynamoDB resource not available (credentials/region issue): {e}")
        return None


def save_to_dynamodb(chat_id: int | str, data: Dict[str, Any]) -> bool:
    """Saves extracted task and message data into Amazon DynamoDB or local memory fallback."""
    timestamp = int(time.time())
    iso_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))

    item = {
        "PK": f"USER#{chat_id}",
        "SK": f"MSG#{timestamp}",
        "chat_id": str(chat_id),
        "created_at": iso_time,
        "summary": data.get("summary", ""),
        "category": data.get("category", "Other"),
        "tasks": data.get("tasks", []),
        "tool_results": data.get("tool_results", []),
        "raw_text": data.get("raw_text", ""),
    }

    dynamodb = get_dynamodb_resource()
    if not dynamodb:
        logger.info(f"Local Dev Mode: Saving item into local memory store for USER#{chat_id}")
        _LOCAL_STORAGE.append(item)
        return True

    try:
        table = dynamodb.Table(TABLE_NAME)
        table.put_item(Item=item)
        logger.info(f"Saved message record to DynamoDB table '{TABLE_NAME}' under USER#{chat_id}")
        return True
    except Exception as e:
        logger.warning(f"DynamoDB put_item failed ({e}). Falling back to local memory store.")
        _LOCAL_STORAGE.append(item)
        return True


def get_user_history(chat_id: int | str, limit: int = 10) -> List[Dict[str, Any]]:
    """Retrieves recent message history for a user from DynamoDB or local memory store."""
    dynamodb = get_dynamodb_resource()
    if not dynamodb:
        return [item for item in reversed(_LOCAL_STORAGE) if item.get("chat_id") == str(chat_id)][:limit]

    try:
        table = dynamodb.Table(TABLE_NAME)
        import boto3
        response = table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("PK").eq(f"USER#{chat_id}"),
            ScanIndexForward=False,
            Limit=limit,
        )
        return response.get("Items", [])
    except Exception as e:
        logger.warning(f"DynamoDB query failed ({e}). Returning local memory history.")
        return [item for item in reversed(_LOCAL_STORAGE) if item.get("chat_id") == str(chat_id)][:limit]
