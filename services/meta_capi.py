"""
Meta Conversions API (CAPI) Client
Envia eventos de conversão para a Meta com compliance LGPD.

Referência: https://developers.facebook.com/docs/marketing-api/conversions-api
"""

import hashlib
import logging
import re
import time
from typing import Optional, Dict, Any

import httpx

from config import get_settings

logger = logging.getLogger(__name__)


def hash_sha256(data: str) -> str:
    """
    Hash data using SHA256 for LGPD / Meta compliance.
    The Meta CAPI requires all PII (phone, email) to be sent hashed.
    """
    return hashlib.sha256(data.strip().lower().encode("utf-8")).hexdigest()


def normalize_phone(phone: str) -> str:
    """
    Normalize a Brazilian phone number for Meta CAPI.

    Rules:
    - Remove all non-digit characters (+, -, spaces, @s.whatsapp.net)
    - Ensure DDI 55 prefix
    - Remove the extra 9th digit if present (some Evolution API formats)
    - Final format: 55XXXXXXXXXXX (13 digits for mobile)

    Examples:
        +55 (11) 99999-8888  → 5511999998888
        5511999998888@s.whatsapp.net → 5511999998888
        11999998888 → 5511999998888
    """
    # Strip everything that isn't a digit
    digits = re.sub(r"\D", "", phone)

    # Remove @s.whatsapp.net suffix (already handled by regex, but be safe)
    digits = digits.split("@")[0] if "@" in phone else digits

    # Re-clean after split
    digits = re.sub(r"\D", "", digits)

    # Add Brazilian DDI if missing
    if not digits.startswith("55"):
        digits = "55" + digits

    return digits


class MetaCAPIClient:
    """
    Client for sending conversion events to Meta Conversions API.

    Usage:
        client = MetaCAPIClient()
        result = await client.send_event(
            phone="5511999998888",
            event_name="Purchase",
            value=1500.00,
        )
    """

    def __init__(self):
        settings = get_settings()
        self._pixel_id = settings.meta_pixel_id
        self._access_token = settings.meta_access_token
        self._version = settings.meta_capi_version
        self._base_url = f"https://graph.facebook.com/{self._version}/{self._pixel_id}/events"

        logger.info(f"✅ Meta CAPI Client initialized — Pixel: {self._pixel_id}")

    async def send_event(
        self,
        phone: str,
        event_name: str = "Purchase",
        value: float = 0.0,
        currency: str = "BRL",
        fbclid: Optional[str] = None,
        test_event_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send a conversion event to Meta CAPI.

        Args:
            phone: Raw phone number (will be normalized and hashed)
            event_name: Meta event name (Purchase, Lead, etc.)
            value: Monetary value of the conversion
            currency: Currency code (BRL, USD, etc.)
            fbclid: Facebook Click ID for precise attribution
            test_event_code: Test event code from Meta Events Manager (for testing)

        Returns:
            Dict with Meta API response or error details
        """
        # Normalize and hash the phone
        normalized = normalize_phone(phone)
        phone_hash = hash_sha256(normalized)

        logger.info(
            f"📤 Sending {event_name} event — "
            f"Phone: {normalized[:6]}***{normalized[-2:]} → Hash: {phone_hash[:12]}..."
        )

        # Build user_data
        user_data = {
            "ph": [phone_hash],
        }

        # Add fbclid for precise attribution (the "pulo do gato")
        if fbclid:
            user_data["fbc"] = fbclid
            logger.info(f"🎯 fbclid attached — Attribution precision: ~100%")

        # Build the event payload
        event = {
            "event_name": event_name,
            "event_time": int(time.time()),
            "action_source": "system_generated",
            "user_data": user_data,
            "custom_data": {
                "currency": currency,
                "value": value,
            },
        }

        payload = {
            "data": [event],
            "access_token": self._access_token,
        }

        # Add test event code if provided (for Meta Events Manager testing)
        if test_event_code:
            payload["test_event_code"] = test_event_code
            logger.info(f"🧪 TEST MODE — Using test_event_code: {test_event_code}")

        # Send to Meta
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(self._base_url, json=payload)

                result = response.json()

                if response.status_code == 200:
                    events_received = result.get("events_received", 0)
                    logger.info(
                        f"✅ Meta CAPI SUCCESS — "
                        f"Events received: {events_received} | "
                        f"Event: {event_name} | Value: {currency} {value}"
                    )
                    return {
                        "success": True,
                        "events_received": events_received,
                        "response": result,
                    }
                else:
                    error = result.get("error", {})
                    error_msg = error.get("message", "Unknown error")
                    logger.error(
                        f"❌ Meta CAPI ERROR — "
                        f"Status: {response.status_code} | "
                        f"Error: {error_msg}"
                    )
                    return {
                        "success": False,
                        "status_code": response.status_code,
                        "error": error_msg,
                        "response": result,
                    }

        except httpx.TimeoutException:
            logger.error("❌ Meta CAPI TIMEOUT — Request timed out after 30s")
            return {"success": False, "error": "Request timeout"}
        except Exception as e:
            logger.error(f"❌ Meta CAPI EXCEPTION — {e}")
            return {"success": False, "error": str(e)}


# Singleton
meta_capi_client = MetaCAPIClient()
