import os
import json
import logging
import boto3
from fastapi import FastAPI, Request, HTTPException
from mangum import Mangum

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
SQS_QUEUE_URL = os.environ.get("SQS_QUEUE_URL", "")

app = FastAPI(title="Parent Co-pilot AWS Ingestion Service")


def get_sqs_client():
    return boto3.client("sqs", region_name=AWS_REGION)


@app.post("/webhook")
async def telegram_webhook(request: Request):
    """
    Receives Telegram webhook updates and pushes structured messages to AWS SQS.
    """
    try:
        data = await request.json()
        logger.info(f"Received Telegram webhook update: {data}")

        # Telegram Update specification
        if "message" in data and "text" in data["message"]:
            message_text = data["message"]["text"]
            chat_id = data["message"]["chat"]["id"]

            payload = {
                "source": "telegram",
                "chat_id": chat_id,
                "text": message_text
            }

            if SQS_QUEUE_URL:
                sqs = get_sqs_client()
                response = sqs.send_message(
                    QueueUrl=SQS_QUEUE_URL,
                    MessageBody=json.dumps(payload)
                )
                message_id = response.get("MessageId")
                logger.info(f"Published payload to AWS SQS Queue. MessageId: {message_id}")
            else:
                logger.warning("SQS_QUEUE_URL not configured. Message received but not queued.")

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@app.get("/")
def health_check():
    return {"status": "AWS Ingestion service active", "region": AWS_REGION, "sqs_configured": bool(SQS_QUEUE_URL)}


# Mangum handler for AWS Lambda API Gateway integration
handler = Mangum(app)
