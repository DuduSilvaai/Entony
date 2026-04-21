"""
Entony — Configuration
Loads environment variables with typed validation via pydantic-settings.
"""

import json
import os
from typing import Dict, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


# Default tag → event mapping
DEFAULT_TAG_MAP = {
    "vendido": "Purchase",
    "nome sujo": "Lead_Disqualified_Credit",
    "lead": "LeadSubmitted",
    "carta de credito aprovada": "QualifiedLead",
}


class Settings(BaseSettings):
    """Application settings loaded from .env file."""

    # Meta Conversions API
    meta_pixel_id: str
    meta_access_token: str
    meta_capi_version: str = "v18.0"

    # Evolution API (webhook security)
    evolution_api_key: str = ""

    # Conversion settings
    # Legacy single-tag field (kept for backward compat)
    conversion_tag_name: str = "vendido"
    conversion_event_name: str = "Purchase"

    # Multi-tag map: JSON string of { "tag_name": "MetaEventName" }
    conversion_tag_map_json: str = json.dumps(DEFAULT_TAG_MAP)

    conversion_default_value: float = 0.0
    conversion_currency: str = "BRL"

    # Supabase (audit logging + fbclid lookup)
    supabase_url: str = ""
    supabase_service_key: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 9000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def get_tag_map(self) -> Dict[str, str]:
        """Parse the JSON tag map into a Python dict (lowercase keys)."""
        try:
            raw = json.loads(self.conversion_tag_map_json)
            return {k.strip().lower(): v for k, v in raw.items()}
        except (json.JSONDecodeError, AttributeError):
            # Fallback to legacy single-tag
            return {self.conversion_tag_name.strip().lower(): self.conversion_event_name}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
