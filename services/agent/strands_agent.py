import os
import re
import json
import logging
import urllib.parse
from datetime import date, datetime, timedelta
from typing import List, Optional, Union
from pydantic import BaseModel, Field

from strands import Agent, tool
from strands.models import BedrockModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Structured Output Schemas (Pydantic)
# ---------------------------------------------------------------------------
class TaskItem(BaseModel):
    description: str = Field(..., description="Description of what needs to be done")
    deadline: Optional[str] = Field(default=None, description="Deadline or due date if mentioned (e.g. YYYY-MM-DD or relative text)")
    materials_required: List[str] = Field(default_factory=list, description="Items needed to buy or prepare")


class SchoolMessageAnalysis(BaseModel):
    summary: str = Field(..., description="Brief 1-2 sentence summary of the message and actions taken")
    category: str = Field(..., description="Category: Homework, Logistics, Events, Materials, Exam, or Other")
    tasks: List[TaskItem] = Field(default_factory=list, description="Action items derived from message")
    tool_results: List[str] = Field(default_factory=list, description="Results or links from executed tools")


# ---------------------------------------------------------------------------
# Strands Agent Tools Definitions
# ---------------------------------------------------------------------------
def _get_calendar_credentials():
    """
    Loads Google Service Account credentials.
    Priority:
      1. AWS Secrets Manager secret named by CALENDAR_SA_SECRET_NAME env var.
      2. Local file path from CALENDAR_SA_KEY_PATH env var (for local dev).
    Returns a credentials object or None.
    """
    scopes = ["https://www.googleapis.com/auth/calendar"]

    # 1. Try Secrets Manager (preferred in ECS/Lambda)
    secret_name = os.environ.get("CALENDAR_SA_SECRET_NAME", "")
    if secret_name:
        try:
            import boto3
            region = os.environ.get("AWS_REGION", "us-east-1")
            client = boto3.client("secretsmanager", region_name=region)
            secret_value = client.get_secret_value(SecretId=secret_name)
            sa_info = json.loads(secret_value["SecretString"])
            from google.oauth2 import service_account
            logger.info("Loaded Google SA credentials from AWS Secrets Manager.")
            return service_account.Credentials.from_service_account_info(sa_info, scopes=scopes)
        except Exception as e:
            logger.warning(f"Could not load SA key from Secrets Manager ({secret_name}): {e}")

    # 2. Fallback: local file (dev only)
    calendar_sa_path = os.environ.get("CALENDAR_SA_KEY_PATH", "calendar-sa-key.json")
    if os.path.exists(calendar_sa_path):
        try:
            from google.oauth2 import service_account
            logger.info(f"Loaded Google SA credentials from local file: {calendar_sa_path}")
            return service_account.Credentials.from_service_account_file(calendar_sa_path, scopes=scopes)
        except Exception as e:
            logger.warning(f"Failed to load SA credentials from file {calendar_sa_path}: {e}")

    return None


def _schedule_calendar_event_impl(title: str, date: str, description: str = "") -> str:
    """Creates an all-day event in Google Calendar with proper start and exclusive end dates."""
    logger.info(f"TOOL EXECUTED: schedule_calendar_event(title='{title}', date='{date}')")
    parent_email = os.environ.get("PARENT_EMAIL", "")

    # Clean and validate date string
    date_clean = date.strip().split("T")[0]
    try:
        start_d = datetime.strptime(date_clean, "%Y-%m-%d").date()
    except Exception:
        # Fallback to tomorrow if model returned an unparseable format
        logger.warning(f"Invalid date format '{date}', defaulting to tomorrow.")
        start_d = date.today() + timedelta(days=1)

    # In Google Calendar API v3, an all-day event's end.date is EXCLUSIVE (end = start + 1 day)
    end_d = start_d + timedelta(days=1)
    start_date_str = start_d.strftime("%Y-%m-%d")
    end_date_str = end_d.strftime("%Y-%m-%d")

    try:
        from googleapiclient.discovery import build
        credentials = _get_calendar_credentials()
        if not credentials:
            logger.warning("No Google Calendar credentials found. Returning mock response.")
            return f"📅 [Mock Calendar] Created event '{title}' on {start_date_str}. (Set CALENDAR_SA_SECRET_NAME to enable real events)"

        service = build("calendar", "v3", credentials=credentials)

        event_body = {
            "summary": title,
            "description": description,
            "start": {"date": start_date_str, "timeZone": "Asia/Kolkata"},
            "end": {"date": end_date_str, "timeZone": "Asia/Kolkata"},
        }
        if parent_email:
            event_body["attendees"] = [{"email": parent_email}]

        event = service.events().insert(calendarId="primary", body=event_body, sendUpdates="all").execute()
        link = event.get("htmlLink", "")
        return f"📅 Created Google Calendar event '{title}' on {start_date_str}. Link: {link}"
    except Exception as e:
        logger.error(f"Calendar API error: {e}")
        return f"⚠️ Failed to create calendar event: {str(e)}"


def _search_materials_impl(items: Union[List[str], str]) -> str:
    """Generates e-commerce & quick commerce search links (Amazon, Blinkit, Zepto) for requested school materials."""
    logger.info(f"TOOL EXECUTED: search_materials(items={items})")
    # Robustly handle if LLM passed a string instead of a list
    if isinstance(items, str):
        clean_items = [i.strip() for i in items.split(",") if i.strip()]
    elif isinstance(items, (list, tuple)):
        clean_items = [str(i).strip() for i in items if str(i).strip()]
    else:
        clean_items = [str(items).strip()] if items else []

    if not clean_items:
        return ""

    query = urllib.parse.quote_plus(" ".join(clean_items))
    amazon_url = f"https://www.amazon.in/s?k={query}"
    blinkit_url = f"https://blinkit.com/s/?q={query}"
    zepto_url = f"https://www.zeptonow.com/search?query={query}"
    items_display = ", ".join(clean_items)

    return (
        f"🛒 Shopping links for materials ({items_display}):\n"
        f"  • Amazon: {amazon_url}\n"
        f"  • Blinkit (10-min): {blinkit_url}\n"
        f"  • Zepto (10-min): {zepto_url}"
    )


def _generate_study_quiz_impl(topic: str, key_concepts: str = "", num_questions: int = 3) -> str:
    """Generates a 3-question revision quiz with answers for the announced test or quiz topic."""
    logger.info(f"TOOL EXECUTED: generate_study_quiz(topic='{topic}', key_concepts='{key_concepts}')")
    try:
        import boto3
        region = os.environ.get("AWS_REGION", "us-east-1")
        model_id = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0")
        client = boto3.client("bedrock-runtime", region_name=region)

        prompt = (
            f"Generate exactly {num_questions} concise, kid-friendly practice quiz questions with clear answers "
            f"for a school student on the topic: '{topic}'. "
            f"{f'Focus areas: {key_concepts}' if key_concepts else ''}\n"
            f"Format strictly as:\n"
            f"Q1: [Question]\n"
            f"Ans: [Short Answer]\n\n"
            f"Q2: [Question]\n"
            f"Ans: [Short Answer]\n\n"
            f"Q3: [Question]\n"
            f"Ans: [Short Answer]"
        )

        response = client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 450, "temperature": 0.5}
        )
        quiz_text = response["output"]["message"]["content"][0]["text"].strip()
        return f"📝 *Practice Quiz on '{topic}':*\n{quiz_text}"
    except Exception as e:
        logger.warning(f"Bedrock direct quiz call fallback: {e}")
        return (
            f"📝 *Practice Quiz on '{topic}':*\n"
            f"Q1: What is the main definition and key concept of {topic}?\n"
            f"Ans: Review the primary lesson summary and definitions in the textbook.\n\n"
            f"Q2: Give two important examples or applications of {topic}.\n"
            f"Ans: Recall classroom demonstrations and chapter illustrations.\n\n"
            f"Q3: What are 3 keywords or formulas to remember for {topic}?\n"
            f"Ans: Check highlighted vocabulary terms at the end of the chapter."
        )


@tool
def schedule_calendar_event(title: str, date: str, description: str = "") -> str:
    """Schedules a new event in the parent's Google Calendar and sends an invite.
    MANDATORY whenever any test, quiz, exam, deadline, submission, school event, or meeting is mentioned.

    Args:
        title: The title of the event (e.g., 'Science Quiz', 'Math Exam Prep', 'Art Project Submission').
        date: The date of the event in YYYY-MM-DD format.
        description: Optional details or syllabus about the event.
    """
    return _schedule_calendar_event_impl(title=title, date=date, description=description)


@tool
def search_materials(items: List[str]) -> str:
    """Searches online for where to buy required school materials and generates shopping links for Amazon, Blinkit, and Zepto.
    MANDATORY whenever any physical items, stationery, craft materials, or books to buy are mentioned.

    Args:
        items: List of physical items, stationery, craft materials, or books to buy (e.g., ['blue chart paper', 'glue stick']).
    """
    return _search_materials_impl(items)


@tool
def generate_study_quiz(topic: str, key_concepts: str = "") -> str:
    """Generates 3 quick practice revision questions and answers for the child to prepare for an upcoming quiz, exam, or test.
    MANDATORY whenever an exam, test, quiz, or study topic is announced.

    Args:
        topic: The topic, subject, or chapter of the exam/quiz (e.g., 'Science - Plants', 'Math - Fractions').
        key_concepts: Optional key terms, chapters, or syllabus points mentioned in the message.
    """
    return _generate_study_quiz_impl(topic=topic, key_concepts=key_concepts)


AGENT_TOOLS = [schedule_calendar_event, search_materials, generate_study_quiz]


SYSTEM_PROMPT_TEMPLATE = """
You are an intelligent Educational & Administrative Co-pilot for parents.
Your job is to analyze messages sent by school teachers, extract actionable items, and ALWAYS execute the appropriate tools.

Today's date is: {today}  (use this to resolve relative dates like 'tomorrow', 'this Friday', 'next week', etc.)

Date Inference Rules:
- If no date is mentioned at all, assume the event or deadline is TOMORROW ({tomorrow}).
- If a day name is mentioned (e.g., 'Tuesday'), resolve it to the NEAREST UPCOMING date from today.
- If a specific date is mentioned, use it exactly.
- Always output dates in YYYY-MM-DD format.

CRITICAL TOOL CALLING RULES:
1. `schedule_calendar_event` is MANDATORY:
   - If there is ANY exam, test, quiz, project submission, school event, field trip, parent-teacher meeting, or deadline mentioned, you MUST call `schedule_calendar_event`.
2. `search_materials` is MANDATORY:
   - If ANY physical items, stationery, supplies, chart papers, craft items, books, uniform items, or materials are mentioned, you MUST call `search_materials`.
3. `generate_study_quiz` is MANDATORY:
   - If ANY exam, test, quiz, assessment, or study topic is announced (e.g., 'Science quiz on Tuesday'), you MUST call `generate_study_quiz` to create practice questions for the child.
4. MULTI-TOOL EXECUTION (CRITICAL):
   - A single teacher message can and should trigger MULTIPLE tools simultaneously (e.g. a quiz message requires `schedule_calendar_event` for the date, `search_materials` if chart papers/glue are needed, AND `generate_study_quiz` for practice questions)!
   - Call ALL applicable tools before generating the final answer.

Context — Today's Already Processed Messages (do not duplicate these tasks):
{history_context}

Instructions:
1. Analyze the teacher message below.
2. Call all relevant tools (`schedule_calendar_event`, `search_materials`, and/or `generate_study_quiz`).
3. Output ONLY a valid JSON object matching this schema:
{{
    "summary": "Brief 1-2 sentence summary of the message and actions taken.",
    "category": "One of [Homework, Logistics, Events, Materials, Exam, Other]",
    "tasks": [
        {{
            "description": "Action item description",
            "deadline": "YYYY-MM-DD resolved date",
            "materials_required": ["item 1", "item 2"]
        }}
    ],
    "tool_results": ["Exact string output returned by each tool called"]
}}
Do NOT include markdown formatting or conversational text outside the JSON. Return pure JSON only.
"""


def _extract_json(text: str) -> dict:
    """Extracts and parses JSON from LLM output, handling markdown blocks or extraneous text."""
    text = text.strip()
    # 1. Try direct parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # 2. Try markdown fenced block ```json ... ``` or ``` ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except Exception:
            pass

    # 3. Find outermost curly braces { ... }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(text[first_brace : last_brace + 1].strip())
        except Exception:
            pass

    raise ValueError(f"Could not parse valid JSON from agent response: {text[:200]}")


# ---------------------------------------------------------------------------
# Main Strands Agent Processing Function
# ---------------------------------------------------------------------------
def process_message_with_strands(text: str, history: list = None) -> dict:
    """Executes the Strands Agent to analyze teacher messages, run tools, and return structured JSON.
    
    Args:
        text: The incoming teacher message to process.
        history: Optional list of today's previously processed message dicts for context.
    """
    model_id = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0")
    region = os.environ.get("AWS_REGION", "us-east-1")

    try:
        today = date.today()
        tomorrow = today + timedelta(days=1)

        # Build history context string for the prompt
        if history:
            history_lines = []
            for h in history:
                history_lines.append(f"- [{h.get('category','?')}] {h.get('summary','')} (tasks: {len(h.get('tasks',[]))})")
            history_context = "\n".join(history_lines)
        else:
            history_context = "No messages processed yet today."

        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            today=today.strftime("%Y-%m-%d (%A)"),
            tomorrow=tomorrow.strftime("%Y-%m-%d"),
            history_context=history_context
        )

        logger.info(f"Initializing Strands Agent with model '{model_id}' | History items: {len(history or [])}")
        bedrock_model = BedrockModel(model_id=model_id, region_name=region)

        agent = Agent(
            model=bedrock_model,
            system_prompt=system_prompt,
            tools=AGENT_TOOLS
        )

        response = agent(f'Teacher Message:\n"{text}"')

        # Get text response from Strands Agent output
        if hasattr(response, 'text'):
            res_text = response.text
        else:
            res_text = str(response)

        parsed = _extract_json(res_text)

        # Enforce Mandatory E-commerce link safety net:
        # If tasks have materials_required, ensure a shopping search link is in tool_results
        tasks = parsed.get("tasks", [])
        all_materials = []
        for t in tasks:
            for m in t.get("materials_required", []):
                if m and m not in all_materials:
                    all_materials.append(m)

        tool_results = parsed.setdefault("tool_results", [])
        has_search_link = any("amazon.in" in str(r) or "Search link" in str(r) for r in tool_results)

        if all_materials and not has_search_link:
            logger.info(f"Enforcing mandatory e-commerce link for materials: {all_materials}")
            fallback_link = _search_materials_impl(all_materials)
            if fallback_link:
                tool_results.append(fallback_link)

        # Enforce Mandatory Calendar Event safety net:
        # If tasks have deadlines or category is Exam/Events/Homework, ensure calendar event is in tool_results
        has_calendar_event = any("Google Calendar event" in str(r) or "Mock Calendar" in str(r) for r in tool_results)
        category = parsed.get("category", "")

        if not has_calendar_event:
            target_title = None
            target_date = None
            for t in tasks:
                d = t.get("deadline")
                if d and re.match(r"^\d{4}-\d{2}-\d{2}$", d.strip()):
                    target_title = t.get("description")
                    target_date = d.strip()
                    break

            if not target_date and category in ["Exam", "Events", "Homework"]:
                target_title = parsed.get("summary", "School Event")
                target_date = tomorrow.strftime("%Y-%m-%d")

            if target_date:
                logger.info(f"Enforcing mandatory calendar event: '{target_title}' on {target_date}")
                cal_fallback = _schedule_calendar_event_impl(
                    title=target_title or "School Activity",
                    date=target_date,
                    description=text[:200]
                )
                if cal_fallback:
                    tool_results.append(cal_fallback)

        # Enforce Mandatory Practice Quiz safety net:
        # If category is Exam or message mentions quiz/test/exam, ensure a study quiz is in tool_results
        has_quiz = any("Practice Quiz" in str(r) for r in tool_results)
        is_quiz_mentioned = (
            category == "Exam"
            or any(w in text.lower() for w in ["quiz", "test", "exam", "midterm", "assessment", "syllabus"])
        )

        if not has_quiz and is_quiz_mentioned:
            topic_candidate = None
            for t in tasks:
                desc = t.get("description", "")
                if any(w in desc.lower() for w in ["quiz", "test", "exam", "prepare", "study", "oral"]):
                    topic_candidate = desc
                    break
            if not topic_candidate:
                topic_candidate = parsed.get("summary", "Upcoming School Quiz")

            logger.info(f"Enforcing mandatory practice quiz fallback for: '{topic_candidate}'")
            quiz_fallback = _generate_study_quiz_impl(topic=topic_candidate)
            if quiz_fallback:
                tool_results.append(quiz_fallback)

        logger.info("Strands Agent successfully processed message.")
        return parsed

    except Exception as e:
        logger.error(f"Strands Agent invocation error: {e}", exc_info=True)
        return {"error": str(e), "raw_text": text}
