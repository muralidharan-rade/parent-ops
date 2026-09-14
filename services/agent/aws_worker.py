import os
import json
import logging
import requests
import boto3
from strands_agent import process_message_with_strands
from dynamo_storage import save_to_dynamodb

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment Configuration
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
SQS_QUEUE_URL = os.environ.get("SQS_QUEUE_URL", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")


def send_telegram_message(chat_id: int | str, text: str):
    """Sends a formatted markdown response back to the user via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning(f"TELEGRAM_BOT_TOKEN not set. Console output:\n{text}")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info(f"Telegram reply sent successfully to chat_id={chat_id}")
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")


def format_telegram_reply(data: dict) -> str:
    """Formats structured analysis into a clean markdown string for Telegram."""
    summary = data.get("summary", "")
    tasks = data.get("tasks", [])
    tool_results = data.get("tool_results", [])

    reply = f"🤖 *Parent Co-pilot*\n\n{summary}\n"

    # Separate standard actions (calendar, shopping) and study quizzes
    actions = []
    quizzes = []
    for result in tool_results:
        if "Practice Quiz" in result:
            quizzes.append(result)
        else:
            actions.append(result)

    if actions:
        reply += "\n*Actions Executed:*\n"
        for result in actions:
            reply += f"🔹 {result}\n"

    if tasks:
        reply += "\n*Action Items / To-Do:*\n"
        for task in tasks:
            desc = task.get("description", "")
            deadline = task.get("deadline")
            materials = task.get("materials_required", [])

            reply += f"▪️ {desc}"
            if deadline:
                reply += f" _(Due: {deadline})_"
            if materials:
                reply += f"\n  🛍 Materials needed: {', '.join(materials)}"
            reply += "\n"

    if quizzes:
        for quiz in quizzes:
            reply += f"\n{quiz}\n"

    return reply


def process_single_message_payload(payload: dict):
    """Processes a decoded JSON message payload from SQS or Webhook."""
    chat_id = payload.get("chat_id")
    text = payload.get("text")

    if not chat_id or not text:
        logger.warning("Message missing chat_id or text payload. Skipping.")
        return

    logger.info(f"Processing message for chat_id={chat_id}: {text[:80]}...")

    # Fetch today's history for context-aware deduplication and aggregation
    from dynamo_storage import get_user_history
    from datetime import date
    today_str = date.today().strftime("%Y-%m-%d")
    all_history = get_user_history(chat_id, limit=20)
    todays_history = [h for h in all_history if h.get("created_at", "").startswith(today_str)]
    logger.info(f"Loaded {len(todays_history)} history item(s) from today for context.")

    # Run Strands Agent processing with today's context
    extracted_data = process_message_with_strands(text, history=todays_history)

    if "error" in extracted_data:
        logger.error(f"Strands Agent returned error: {extracted_data['error']}")
        send_telegram_message(chat_id, "⚠️ Sorry, I had trouble processing that message. Please try again.")
    else:
        extracted_data["raw_text"] = text
        # Save to DynamoDB
        save_to_dynamodb(chat_id, extracted_data)

        # Send response via Telegram
        reply_markdown = format_telegram_reply(extracted_data)
        send_telegram_message(chat_id, reply_markdown)


def lambda_handler(event, context):
    """AWS Lambda entrypoint for SQS Event Source Mapping."""
    logger.info(f"Lambda triggered with event records count: {len(event.get('Records', []))}")

    for record in event.get("Records", []):
        try:
            body_str = record.get("body", "{}")
            payload = json.loads(body_str)
            process_single_message_payload(payload)
        except Exception as e:
            logger.error(f"Error processing record {record.get('messageId')}: {e}", exc_info=True)

    return {"statusCode": 200, "body": json.dumps({"status": "processed"})}


def run_sqs_poller():
    """Continuous polling loop for running worker as a daemon container or local process."""
    if not SQS_QUEUE_URL:
        logger.error("SQS_QUEUE_URL environment variable is required to run poller mode.")
        return

    sqs = boto3.client("sqs", region_name=AWS_REGION)
    logger.info(f"Starting AWS SQS Worker Poller on queue: {SQS_QUEUE_URL}")

    while True:
        try:
            response = sqs.receive_message(
                QueueUrl=SQS_QUEUE_URL,
                MaxNumberOfMessages=5,
                WaitTimeSeconds=20,
                AttributeNames=["All"]
            )

            messages = response.get("Messages", [])
            for message in messages:
                receipt_handle = message.get("ReceiptHandle")
                body = json.loads(message.get("Body", "{}"))

                process_single_message_payload(body)

                # Delete processed message from SQS
                sqs.delete_message(QueueUrl=SQS_QUEUE_URL, ReceiptHandle=receipt_handle)

        except KeyboardInterrupt:
            logger.info("Poller stopped by user.")
            break
        except Exception as e:
            logger.error(f"SQS polling error: {e}", exc_info=True)


if __name__ == "__main__":
    run_sqs_poller()
