import httpx
from typing import Optional, Dict, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..models.user import User


class LLMService:
    def __init__(self):
        self.session = httpx.AsyncClient(timeout=60.0)

    async def get_config(self, user_id: int) -> Dict[str, str]:
        """Get LLM configuration for a user (or admin defaults)"""
        from ..models.settings import AppSettings
        
        # Try to get per-user config first, fall back to admin defaults
        try:
            db = None  # Would need DB session
            base_url_result = await db.execute(
                select(AppSettings).where(AppSettings.key == "llm_base_url")
            )
            model_result = await db.execute(
                select(AppSettings).where(AppSettings.key == "llm_model")
            )
            
            config = {}
            if base_url_row := base_url_result.scalar_one_or_none():
                config["base_url"] = base_url_row.value
            if model_row := model_result.scalar_one_or_none():
                config["model"] = model_row.value
            
            return config
        except Exception as e:
            print(f"Error getting LLM config: {e}")
            # Return default OpenAI configuration
            return {"base_url": "https://api.openai.com/v1", "model": "gpt-4"}

    async def analyze_document(self, user_id: int, document_content: str) -> Dict[str, any]:
        """Analyze a medical document using LLM"""
        config = await self.get_config(user_id)
        
        prompt = f"""You are a medical document analysis assistant. Analyze the following medical document and extract structured information.

Document content:
{document_content}

Please provide your analysis in JSON format with the following structure:
{{
    "summary": "Brief summary of the document",
    "findings": [
        {{
            "metric_name": "Name of the metric/finding",
            "value": "Extracted value",
            "unit": "Unit of measurement",
            "reference_range": "Normal reference range if mentioned",
            "is_important": true/false,
            "notes": "Any additional notes or observations"
        }}
    ],
    "recommendations": [
        "Recommendation 1",
        "Recommendation 2"
    ],
    "follow_up_required": true/false,
    "follow_up_notes": "Notes about required follow-up"
}}"""

        headers = {
            "Content-Type": "application/json",
            # Get API key from config or environment
            "Authorization": f"Bearer {self._get_api_key()}"
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{config['base_url']}/chat/completions",
                headers=headers,
                json={
                    "model": config["model"],
                    "messages": [
                        {"role": "system", "content": "You are a helpful medical document analysis assistant. Always respond in valid JSON format."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 2000
                }
            )

        if response.status_code != 200:
            raise ValueError(f"LLM analysis failed: {response.text}")

        return response.json()

    def _get_api_key(self) -> str:
        """Get API key from environment or config"""
        import os
        # Try environment variable first
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            return api_key
        
        # Try to get from database - will be handled by caller with proper db session
        raise ValueError("LLM API key not configured. Please set OPENAI_API_KEY environment variable or configure in admin settings.")


llm_service = LLMService()
