"""
Entony — Configuration
Loads environment variables with typed validation via pydantic-settings.
"""

import json
import os
from typing import Dict
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from .env file."""

    # Meta Conversions API
    meta_pixel_id: str
    meta_access_token: str
    meta_capi_version: str = "v18.0"

    # Evolution API (webhook security)
    evolution_api_key: str = ""

    # Conversion settings — multi-label map (preferred)
    # JSON string: '{"vendido":"Purchase","lead":"LeadSubmitted"}'
    conversion_tag_map_json: str = ""

    # Legacy single-mapping (used as fallback if conversion_tag_map_json is empty)
    conversion_tag_name: str = "Pago"
    conversion_event_name: str = "Purchase"

    conversion_default_value: float = 0.0
    conversion_currency: str = "BRL"

    # Supabase (audit logging + fbclid lookup)
    supabase_url: str = ""
    supabase_service_key: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 9000

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def conversion_tag_map(self) -> Dict[str, str]:
        """Returns label→event map.

        Uses CONVERSION_TAG_MAP_JSON if set; falls back to the legacy
        CONVERSION_TAG_NAME / CONVERSION_EVENT_NAME single-mapping.
        """
        if self.conversion_tag_map_json:
            try:
                return json.loads(self.conversion_tag_map_json)
            except json.JSONDecodeError:
                pass
        return {self.conversion_tag_name: self.conversion_event_name}


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings singleton."""
    return Settings()
