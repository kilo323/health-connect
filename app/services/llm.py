import httpx
import json
import os
from typing import Dict, Any
from ..database import async_session_factory
from ..models.settings import AppSettings


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

        return {
            "base_url": os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1"),
            "api_key": os.getenv("OPENAI_API_KEY", ""),
            "model": os.getenv("OPENAI_MODEL", "gpt-4"),
        }

    async def analyze_document(self, user_id: int, document_content: str) -> Dict[str, Any]:
        """Analyze a medical document using LLM"""
        config = await self.get_config()

        prompt = f"""You are a medical document analysis assistant. Analyze the following medical document and extract structured information.

Document content:
{document_content[:4000]}

Please provide your analysis in JSON format with the following structure:
{{
    "summary": "Brief summary of the document",
    "findings": [
        {{
            "metric_name": "Name of the metric/finding",
            "value": "Extracted value",
            "unit": "Unit of measurement",
            "reference_range": "Normal reference range if mentioned",
            "is_important": true,
            "notes": "Any additional notes or observations"
        }}
    ],
    "recommendations": [
        "Recommendation 1",
        "Recommendation 2"
    ],
    "follow_up_required": true,
    "follow_up_notes": "Notes about required follow-up"
}}"""

        base_url = config.get("base_url", "").rstrip("/")
        api_key = config.get("api_key", "")
        model = config.get("model", "gpt-4")

        if not api_key:
            raise ValueError("LLM API key not configured. Ask an admin to set up LLM configuration.")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You are a helpful medical document analysis assistant. Always respond in valid JSON format."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 8192,
                },
            )

        if response.status_code != 200:
            raise ValueError(f"LLM analysis failed ({response.status_code}): {response.text[:200]}")

        result = response.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        import logging
        _log = logging.getLogger("llm_debug")
        _log.warning(f"LLM status={response.status_code}, content_len={len(content)}, keys={list(result.keys())}")
        if not content:
            _log.warning(f"LLM full response: {json.dumps(result)[:500]}")
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
            import re
            cleaned = re.sub(r'^```(?:json)?\s*', '', content.strip())
            cleaned = re.sub(r'\s*```$', '', cleaned)
            try:
                return json.loads(cleaned)
            except (json.JSONDecodeError, KeyError):
                return {"summary": content[:500], "findings": [], "recommendations": [], "follow_up_required": False, "follow_up_notes": ""}


llm_service = LLMService()
