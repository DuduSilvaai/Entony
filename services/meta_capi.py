"""
Entony — Meta Conversions API (CAPI) Client

Sends server-side conversion events to Meta (Facebook) for ad attribution.
Uses the Conversions API v18.0+.

Docs: https://developers.facebook.com/docs/marketing-api/conversions-api
"""

import hashlib
import logging
import re
import time
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("entony")


def hash_sha256(value: str) -> str:
    """SHA-256 hash a value (Meta CAPI requirement for PII)."""
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def normalize_phone(raw: str) -> str:
    """
    Normalize a phone number for Meta CAPI.

    Handles:
    - WhatsApp JID format: 5511999998888@s.whatsapp.net
    - Raw digits: 5511999998888
    - Formatted: +55 (11) 99999-8888

    Returns digits only, with country code (e.g., '5511999998888').
    """
    # Strip WhatsApp JID suffix
    phone = raw.split("@")[0] if "@" in raw else raw

    # Remove all non-digit characters
    phone = re.sub(r"\D", "", phone)

    # If it doesn't start with a country code, assume Brazil (+55)
    if phone and not phone.startswith("55") and len(phone) <= 11:
        phone = "55" + phone

    return phone


class MetaCAPIClient:
    """Client for Meta Conversions API."""

    def __init__(self):
        self._pixel_id: Optional[str] = None
        self._access_token: Optional[str] = None
        self._api_version: str = "v18.0"

    def _ensure_config(self):
        """Lazy-load config to avoid import-time issues."""
        if self._pixel_id is None:
            from config import get_settings
            settings = get_settings()
            self._pixel_id = settings.meta_pixel_id
            self._access_token = settings.meta_access_token
            self._api_version = settings.meta_capi_version

    async def send_event(
        self,
        phone: str,
        event_name: str,
        value: float = 0.0,
        currency: str = "BRL",
        fbclid: Optional[str] = None,
        test_event_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send a conversion event to Meta Conversions API.

        Args:
            phone: Normalized phone number (digits only, with country code).
            event_name: Meta event name (e.g., 'Purchase', 'Lead').
            value: Monetary value of the conversion.
            currency: ISO 4217 currency code.
            fbclid: Facebook click ID for precise attribution.
            test_event_code: Meta test event code (for testing without affecting campaigns).

        Returns:
            Dict with 'success', 'response', and optionally 'error' keys.
        """
        self._ensure_config()

        url = f"https://graph.facebook.com/{self._api_version}/{self._pixel_id}/events"

        # Build user_data with hashed PII (Meta requirement)
        phone_hash = hash_sha256(phone)
        user_data = {"ph": [phone_hash]}

        # Add fbclid for better attribution
        if fbclid:
            user_data["fbc"] = fbclid

        # Build event payload
        event = {
            "event_name": event_name,
            "event_time": int(time.time()),
            "action_source": "system_generated",
            "user_data": user_data,
        }

        # Add custom_data for value-based events
        if value > 0:
            event["custom_data"] = {
                "value": value,
                "currency": currency,
            }

        payload = {
            "data": [event],
            "access_token": self._access_token,
        }

        # Test mode
        if test_event_code:
            payload["test_event_code"] = test_event_code

        # Send to Meta
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload)
                response_data = response.json()

            if response.status_code == 200:
                logger.info(
                    f"✅ Meta CAPI response: {response_data}"
                )
                return {
                    "success": True,
                    "response": response_data,
                    "status_code": response.status_code,
                }
            else:
                error_msg = response_data.get("error", {}).get("message", str(response_data))
                logger.error(
                    f"❌ Meta CAPI error [{response.status_code}]: {error_msg}"
                )
                return {
                    "success": False,
                    "error": error_msg,
                    "response": response_data,
                    "status_code": response.status_code,
                }

        except httpx.TimeoutException:
            logger.error("❌ Meta CAPI request timed out")
            return {"success": False, "error": "Request timed out"}
        except Exception as e:
            logger.error(f"❌ Meta CAPI unexpected error: {e}")
            return {"success": False, "error": str(e)}


# Singleton instance
meta_capi_client = MetaCAPIClient()
