import httpx
import json
import os
import re
import logging
from pathlib import Path
from typing import Dict, Any
from ..database import async_session_factory
from ..models.settings import AppSettings

logger = logging.getLogger(__name__)

# Resolve the data directory relative to the project root.
# On the host the layout is  <project>/app/services/llm.py  →  <project>/data/
# In Docker the layout is     /app/services/llm.py           →  /app/data/
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
if not _DATA_DIR.exists():
    _DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_PROMPT_FILE = _DATA_DIR / "llm_prompt.md"

# Default prompt used as fallback if the file is missing
_DEFAULT_PROMPT = """You are a medical document analysis assistant. Analyze the following medical document and extract ALL available structured information.

Document content:
{document_content}

IMPORTANT — DATE EXTRACTION: Look for the date the test, lab, or examination was performed. Common labels include "Date of Service", "Collection Date", "Test Date", "Specimen Date", "Report Date", "Scan Date" (DEXA). This is the date the health data was actually collected, NOT the date the document was uploaded or received. If you find multiple dates, use the specimen/test/scan collection date. If no date can be determined, set test_date to null.

EXTRACTION RULES — follow these precisely:
1. Extract EVERY measurable metric, value, score, percentage, mass measurement, and quantitative result found in the document. Do NOT skip any metric.
2. Values should be numeric whenever possible. If a value cannot be parsed as a number (e.g. "Positive", "<0.1", "Critical"), store it as a string and add a note explaining why.
3. Include the reference/normal range whenever the document provides one.
4. For result flags (H, L, HH, LL, A, abnormal, etc.), record the flag in the notes field and set is_important to true.
5. Use concise, consistent metric names (e.g. "Weight", "BMI", "Body Fat Percentage", "Bone Mass", "Body Water", "Basal Metabolic Rate"). Include the body region in segmental metrics (e.g. "Right Arm Fat", "Trunk Muscle").

is_important RULE: Set is_important to true ONLY when the value is outside the reference range, flagged as abnormal/high/low/critical by the lab, or represents a clinically significant finding. Set to false when the value is within normal range or not flagged.

FOR BODY COMPOSITION / DEXA / INBODY REPORTS, extract ALL of the following (if present):
- General: weight, BMI, body fat percentage, body fat mass, fat-free mass, lean body mass, muscle weight/mass, bone mass, body water (total and percentage), protein mass, basal metabolic rate (BMR), visceral fat level/area, skeletal muscle mass, fitness score
- DEXA-specific: bone mineral density (BMD) for each site (spine, hip, femoral neck, etc.), T-score, Z-score, fracture risk assessment
- Segmental values (right arm, left arm, trunk, right leg, left leg) for both fat and muscle mass

FOR LAB / BLOOD WORK REPORTS, extract EVERY test result line — not just abnormal ones. Include any flags (H, L, A, etc.) in the notes field.

Please provide your analysis in JSON format with the following structure:
{{
    "summary": "Brief summary of the document",
    "test_date": "YYYY-MM-DD format date when the test/exam was performed, or null if unknown",
    "findings": [
        {{
            "metric_name": "Name of the metric/finding",
            "value": "Extracted numeric value (or string if not numeric)",
            "unit": "Unit of measurement",
            "reference_range": "Normal reference range if mentioned",
            "is_important": false,
            "notes": "Any flags (H/L/A), observations, or context"
        }}
    ],
    "recommendations": [
        "Recommendation 1"
    ],
    "follow_up_required": false,
    "follow_up_notes": "Notes about required follow-up"
}}"""


def get_prompt_file_path() -> Path:
    """Return the path to the LLM prompt file."""
    return _PROMPT_FILE


def load_prompt_template() -> str:
    """Load the prompt template from disk (no caching). Returns the raw template string."""
    try:
        return _PROMPT_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning(f"Prompt file not found at {_PROMPT_FILE}, using default prompt")
        return _DEFAULT_PROMPT


def save_prompt_template(content: str) -> None:
    """Save the prompt template to disk."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _PROMPT_FILE.write_text(content, encoding="utf-8")
    logger.info(f"Prompt template saved to {_PROMPT_FILE}")


class LLMService:

    async def get_config(self) -> Dict[str, str]:
        """Get LLM configuration from admin settings"""
        async with async_session_factory() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(AppSettings).where(AppSettings.key == "llm_config")
            )
            config_row = result.scalar_one_or_none()

        if config_row:
            try:
                return json.loads(config_row.value)
            except (json.JSONDecodeError, TypeError):
                pass

        # Fall back to environment variables from .env / docker-compose
        return {
            "base_url": os.getenv("LLM_URL", ""),
            "api_key": os.getenv("LLM_API_TOKEN", ""),
            "model": os.getenv("LLM_MODEL", ""),
        }

    async def analyze_document(self, user_id: int, document_content: str = "", image_content: list[str] | None = None) -> Dict[str, Any]:
        """Analyze a medical document using LLM.

        Args:
            user_id: The user ID.
            document_content: Extracted text content (used for text-based documents).
            image_content: List of base64-encoded image strings (used for scanned
                PDFs and image documents). Requires a vision-capable model.
        """
        config = await self.get_config()

        # Load prompt template from file on every call (no caching)
        template = load_prompt_template()

        base_url = config.get("base_url", "").rstrip("/")
        api_key = config.get("api_key", "")
        model = config.get("model", "gpt-4")

        if not api_key:
            raise ValueError("LLM API key not configured. Ask an admin to set up LLM configuration.")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        # Build user message — multimodal if images are provided
        if image_content:
            content_parts: list[dict] = []
            # Add each image as an image_url part
            for img_b64 in image_content:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{img_b64}",
                        "detail": "high",
                    },
                })
            # Add the text prompt
            prompt_text = template.replace("{document_content}", "See attached image(s)")
            content_parts.append({"type": "text", "text": prompt_text})
            user_message = {"role": "user", "content": content_parts}
        else:
            prompt = template.replace("{document_content}", document_content[:16000])
            user_message = {"role": "user", "content": prompt}

        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You are a helpful medical document analysis assistant. Always respond in valid JSON format."},
                        user_message,
                    ],
                    "temperature": 0.3,
                    "max_tokens": 16384,
                },
            )

        if response.status_code != 200:
            raise ValueError(f"LLM analysis failed ({response.status_code}): {response.text[:200]}")

        result = response.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        logger.debug(f"LLM status={response.status_code}, content_len={len(content)}, keys={list(result.keys())}")
        if not content:
            logger.warning(f"LLM full response: {json.dumps(result)[:500]}")
        try:
            parsed = json.loads(content)
            # Handle case where model returns double-encoded JSON
            if isinstance(parsed, dict) and "summary" in parsed and isinstance(parsed["summary"], str) and parsed["summary"].startswith("{"):
                try:
                    inner = json.loads(parsed["summary"])
                    if isinstance(inner, dict) and "summary" in inner:
                        return inner
                except (json.JSONDecodeError, KeyError):
                    pass
            return parsed
        except (json.JSONDecodeError, KeyError):
            cleaned = re.sub(r'^```(?:json)?\s*', '', content.strip())
            cleaned = re.sub(r'\s*```$', '', cleaned)
            try:
                return json.loads(cleaned)
            except (json.JSONDecodeError, KeyError):
                return {"summary": content[:500], "findings": [], "recommendations": [], "follow_up_required": False, "follow_up_notes": ""}


llm_service = LLMService()
