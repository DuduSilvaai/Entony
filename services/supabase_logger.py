"""
Supabase Logger Service
Handles audit logging of conversion events and fbclid lookup.

If Supabase is not configured, operates in local-only mode (logs to console).
"""

import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from config import get_settings

logger = logging.getLogger(__name__)


class SupabaseLogger:
    """
    Audit logger for Meta CAPI conversion events.

    Responsibilities:
    - Log every conversion dispatch to `meta_conversion_logs` table
    - Look up fbclid from leads table for precise attribution
    """

    def __init__(self):
        settings = get_settings()
        self._client = None
        self._enabled = False

        if settings.supabase_url and settings.supabase_service_key:
            try:
                from supabase import create_client
                self._client = create_client(
                    settings.supabase_url,
                    settings.supabase_service_key,
                )
                self._enabled = True
                logger.info("✅ Supabase Logger initialized — Audit logging enabled")
            except Exception as e:
                logger.warning(f"⚠️ Supabase Logger failed to init: {e} — Running in local-only mode")
        else:
            logger.info("ℹ️ Supabase not configured — Running in local-only mode (logs to console)")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

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
    ) -> Optional[Dict[str, Any]]:
        """
        Log a conversion event dispatch for audit purposes.

        Args:
            phone_hash: SHA256 hash of the normalized phone
            event_name: Meta event name (Purchase, Lead, etc.)
            value: Conversion value
            currency: Currency code
            tag_name: WhatsApp label name that triggered this
            meta_response: Raw response from Meta CAPI
            fbclid: Facebook Click ID if available
            lead_id: UUID of the lead in Supabase
            status: "sent", "error", "skipped"
            error_message: Error description if status is "error"

        Returns:
            The inserted row or None
        """
        log_entry = {
            "phone_hash": phone_hash,
            "event_name": event_name,
            "event_value": value,
            "currency": currency,
            "tag_name": tag_name,
            "meta_response": meta_response,
            "fbclid": fbclid,
            "lead_id": lead_id,
            "status": status,
            "error_message": error_message,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        if not self._enabled:
            logger.info(f"📝 [LOCAL LOG] Conversion: {event_name} | Tag: {tag_name} | Status: {status}")
            return log_entry

        try:
            result = self._client.table("meta_conversion_logs").insert(log_entry).execute()
            if result.data:
                logger.info(f"📝 Conversion logged to Supabase — ID: {result.data[0].get('id')}")
                return result.data[0]
            return log_entry
        except Exception as e:
            logger.warning(f"⚠️ Failed to log conversion to Supabase: {e}")
            return log_entry

    async def find_fbclid_by_phone(self, phone: str) -> Optional[str]:
        """
        Look up fbclid from the leads table by phone number.

        This is the "pulo do gato" — if the lead clicked a Facebook ad,
        the fbclid was captured and stored. Sending it back to Meta
        makes attribution precision close to 100%.

        Args:
            phone: Normalized phone number (digits only, with DDI 55)

        Returns:
            fbclid string if found, None otherwise
        """
        if not self._enabled:
            return None

        try:
            # Try exact match on phone
            result = self._client.table("leads").select(
                "id, fbclid, phone, whatsapp_jid"
            ).or_(
                f"phone.eq.{phone},whatsapp_jid.eq.{phone}@s.whatsapp.net"
            ).limit(1).execute()

            if result.data and len(result.data) > 0:
                lead = result.data[0]
                fbclid = lead.get("fbclid")
                if fbclid:
                    logger.info(f"🎯 fbclid FOUND for phone {phone[:6]}*** — Lead: {lead.get('id')}")
                    return fbclid
                else:
                    logger.info(f"ℹ️ Lead found but no fbclid — Phone: {phone[:6]}***")
            else:
                logger.info(f"ℹ️ No lead found for phone {phone[:6]}***")

            return None

        except Exception as e:
            logger.warning(f"⚠️ Error looking up fbclid: {e}")
            return None

    async def find_lead_id_by_phone(self, phone: str) -> Optional[str]:
        """
        Look up lead UUID by phone number for linking the conversion log.

        Args:
            phone: Normalized phone number

        Returns:
            Lead UUID if found, None otherwise
        """
        if not self._enabled:
            return None

        try:
            result = self._client.table("leads").select("id").or_(
                f"phone.eq.{phone},whatsapp_jid.eq.{phone}@s.whatsapp.net"
            ).limit(1).execute()

            if result.data and len(result.data) > 0:
                return result.data[0].get("id")
            return None

        except Exception as e:
            logger.warning(f"⚠️ Error looking up lead: {e}")
            return None

    async def get_recent_logs(self, limit: int = 50) -> list:
        """
        Fetch recent conversion logs for audit/dashboard.

        Returns:
            List of recent conversion log entries
        """
        if not self._enabled:
            return []

        try:
            result = self._client.table("meta_conversion_logs").select(
                "*"
            ).order(
                "created_at", desc=True
            ).limit(limit).execute()

            return result.data if result.data else []

        except Exception as e:
            logger.warning(f"⚠️ Error fetching conversion logs: {e}")
            return []


# Singleton
supabase_logger = SupabaseLogger()
