"""
Entony — Supabase Audit Logger

Logs conversion events to Supabase for auditing and fbclid lookup.
Gracefully degrades to local-only logging when Supabase is not configured.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("entony")


class SupabaseLogger:
    """
    Supabase client for audit logging and fbclid lookup.

    If Supabase credentials are not configured, all methods are no-ops
    that return empty/default values — the app works without Supabase.
    """

    def __init__(self):
        self._client = None
        self._is_enabled = False
        self._init_attempted = False

    def _ensure_init(self):
        """Lazy-initialize the Supabase client."""
        if self._init_attempted:
            return

        self._init_attempted = True

        try:
            from config import get_settings
            settings = get_settings()

            if not settings.supabase_url or not settings.supabase_service_key:
                logger.info("🗄️ Supabase not configured — running in local-only mode")
                return

            from supabase import create_client
            self._client = create_client(
                settings.supabase_url,
                settings.supabase_service_key,
            )
            self._is_enabled = True
            logger.info("🗄️ Supabase connected successfully")

        except ImportError:
            logger.warning("🗄️ supabase-py not installed — running in local-only mode")
        except Exception as e:
            logger.error(f"🗄️ Supabase init error: {e} — running in local-only mode")

    @property
    def is_enabled(self) -> bool:
        self._ensure_init()
        return self._is_enabled

    async def log_conversion(
        self,
        phone_hash: str,
        event_name: str,
        value: float,
        currency: str,
        tag_name: str,
        meta_response: Dict[str, Any],
        fbclid: Optional[str] = None,
        lead_id: Optional[str] = None,
        status: str = "sent",
        error_message: Optional[str] = None,
    ) -> Optional[Dict]:
        """Log a conversion event to the entony_conversions table."""
        self._ensure_init()

        record = {
            "phone_hash": phone_hash,
            "event_name": event_name,
            "event_value": value,
            "currency": currency,
            "tag_name": tag_name,
            "meta_response": json.dumps(meta_response) if meta_response else None,
            "fbclid": fbclid,
            "lead_id": lead_id,
            "status": status,
            "error_message": error_message,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        if not self._is_enabled:
            logger.info(f"📝 [LOCAL LOG] Conversion: {event_name} | Tag: {tag_name} | Status: {status}")
            return record

        try:
            result = self._client.table("entony_conversions").insert(record).execute()
            logger.info(f"📝 Conversion logged to Supabase: {event_name} | {status}")
            return result.data[0] if result.data else record
        except Exception as e:
            logger.error(f"📝 Failed to log to Supabase: {e}")
            return record

    async def find_fbclid_by_phone(self, phone: str) -> Optional[str]:
        """Look up fbclid from leads table by phone number."""
        self._ensure_init()

        if not self._is_enabled:
            return None

        try:
            result = (
                self._client.table("leads")
                .select("fbclid")
                .eq("phone", phone)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if result.data and result.data[0].get("fbclid"):
                fbclid = result.data[0]["fbclid"]
                logger.info(f"🔗 Found fbclid for phone: {fbclid[:20]}...")
                return fbclid
        except Exception as e:
            logger.debug(f"🔗 fbclid lookup failed: {e}")

        return None

    async def find_lead_id_by_phone(self, phone: str) -> Optional[str]:
        """Look up lead ID from leads table by phone number."""
        self._ensure_init()

        if not self._is_enabled:
            return None

        try:
            result = (
                self._client.table("leads")
                .select("id")
                .eq("phone", phone)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if result.data and result.data[0].get("id"):
                return str(result.data[0]["id"])
        except Exception as e:
            logger.debug(f"🔗 lead_id lookup failed: {e}")

        return None

    async def get_recent_logs(self, limit: int = 50) -> List[Dict]:
        """Fetch recent conversion logs for the audit endpoint."""
        self._ensure_init()

        if not self._is_enabled:
            return []

        try:
            result = (
                self._client.table("entony_conversions")
                .select("*")
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"📝 Failed to fetch logs: {e}")
            return []


# Singleton instance
supabase_logger = SupabaseLogger()
