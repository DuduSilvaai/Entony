"""
Entony — Webhook Listener
Evolution API (WhatsApp Labels) → Meta Conversions API (CAPI)

This microservice listens for label/tag events from Evolution API,
filters for configured tags, and fires conversion events to Meta's
Conversions API for precise ad attribution.

Supports multiple tags via CONVERSION_TAG_MAP_JSON:
  {"vendido": "Purchase", "lead": "LeadSubmitted", ...}

Author: Vibe Energia
"""

import logging
import os
import sys
import json
from typing import Optional, Dict, Any, List
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import get_settings
from services.meta_capi import meta_capi_client, normalize_phone, hash_sha256
from services.supabase_logger import supabase_logger

# ==========================================================================
# LOGGING
# ==========================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("entony")

# ==========================================================================
# APP
# ==========================================================================
app = FastAPI(
    title="Entony — Webhook Listener",
    description="Evolution API → Meta CAPI bridge for conversion tracking",
    version="2.0.0",
)

# CORS (allow all for webhook compatibility)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================================================
# MODELS
# ==========================================================================
class ConversionResponse(BaseModel):
    success: bool
    message: str
    event_name: Optional[str] = None
    phone_hash: Optional[str] = None
    meta_response: Optional[Dict[str, Any]] = None


class ConversionLog(BaseModel):
    id: Optional[str] = None
    phone_hash: str
    event_name: str
    event_value: float
    tag_name: str
    status: str
    created_at: Optional[str] = None


# ==========================================================================
# STARTUP
# ==========================================================================
@app.on_event("startup")
async def startup():
    settings = get_settings()
    tag_map = settings.get_tag_map()

    logger.info("=" * 60)
    logger.info("🚀 ENTONY v2.0 — Webhook Listener starting up...")
    logger.info(f"   📡 Meta Pixel ID: {settings.meta_pixel_id}")
    logger.info(f"   🏷️  Tag Map: {json.dumps(tag_map, ensure_ascii=False)}")
    logger.info(f"   💰 Default Value: {settings.conversion_currency} {settings.conversion_default_value}")
    logger.info(f"   🔑 API Key configured: {'Yes' if settings.evolution_api_key else 'No (open)'}")
    logger.info(f"   🗄️  Supabase: {'Enabled' if supabase_logger.is_enabled else 'Disabled (local mode)'}")
    logger.info(f"   🌐 Server: {settings.host}:{settings.port}")
    logger.info("=" * 60)
    logger.info("✅ Entony ready! Listening for Evolution API label events...")


# ==========================================================================
# HEALTH CHECK
# ==========================================================================
@app.get("/health")
async def health():
    """Health check endpoint."""
    settings = get_settings()
    return {
        "status": "healthy",
        "service": "Entony",
        "version": "2.0.0",
        "meta_pixel_id": settings.meta_pixel_id,
        "tag_map": settings.get_tag_map(),
        "supabase_enabled": supabase_logger.is_enabled,
        "timestamp": datetime.utcnow().isoformat(),
    }


# ==========================================================================
# MAIN WEBHOOK — Evolution API Labels
# ==========================================================================
@app.post("/webhook/whatsapp", response_model=ConversionResponse)
async def webhook_whatsapp(request: Request):
    """
    Receive webhook events from Evolution API.

    Filters for label/tag events and fires conversion events to Meta CAPI
    when a configured tag is applied to a contact.

    The apikey can be sent via:
    - HTTP header: apikey, x-api-key, or Authorization: Bearer <key>
    - Body JSON field: apikey (Evolution API default behavior)

    Configure this URL in your Evolution API webhook settings:
        URL: https://your-domain:9000/webhook/whatsapp
        Events: labels (or all events — non-label events are ignored)
    """
    settings = get_settings()

    # ── 1. Parse raw body ──────────────────────────────────────────────
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Log raw payload for debugging
    logger.info(f"📨 Webhook received — Keys: {list(body.keys())}")
    logger.debug(f"📨 Full payload: {json.dumps(body, ensure_ascii=False, indent=2)}")

    # ── 2. Validate API Key ────────────────────────────────────────────
    #    Evolution API sends the apikey INSIDE the body JSON, not in
    #    HTTP headers. We check both locations for maximum compatibility.
    if settings.evolution_api_key:
        # Try headers first (standard HTTP auth)
        incoming_key = (
            request.headers.get("apikey")
            or request.headers.get("x-api-key")
            or request.headers.get("authorization", "").replace("Bearer ", "").strip()
        )

        # Fallback: read apikey from body JSON (Evolution API behavior)
        if not incoming_key and isinstance(body, dict):
            incoming_key = body.get("apikey", "")

        # Also check query params
        if not incoming_key:
            incoming_key = request.query_params.get("apikey", "")

        incoming_key = (incoming_key or "").strip()

        if incoming_key != settings.evolution_api_key:
            logger.warning(
                f"⛔ Unauthorized webhook attempt — Key mismatch "
                f"(received: '{incoming_key[:8]}...' vs expected: '{settings.evolution_api_key[:8]}...')"
            )
            raise HTTPException(status_code=401, detail="Invalid API key")

        logger.debug("🔑 API key validated successfully")

    # ── 3. Detect event type ───────────────────────────────────────────
    # Evolution API sends different event structures depending on version.
    # We handle multiple formats for maximum compatibility.
    event_type = (
        body.get("event")
        or body.get("type")
        or body.get("action")
        or ""
    ).lower()

    logger.info(f"📋 Event type: \"{event_type}\"")

    # ── 4. Filter for label events ─────────────────────────────────────
    label_event_keywords = ["label", "tag", "etiqueta"]
    is_label_event = any(kw in event_type for kw in label_event_keywords)

    # Also check if the payload structure itself contains label info
    # (some Evolution versions send label data without a specific event type)
    data = body.get("data", body)
    has_label_data = any(
        key in data for key in ["label", "labels", "tag", "tags", "labelName", "tagName"]
    )

    if not is_label_event and not has_label_data:
        logger.info(f"⏭️ Ignoring non-label event: \"{event_type}\"")
        return ConversionResponse(
            success=True,
            message=f"Event '{event_type}' ignored — not a label event",
        )

    # ── 5. Extract label name ──────────────────────────────────────────
    label_name = _extract_label_name(body)

    if not label_name:
        logger.warning("⚠️ Label event detected but couldn't extract label name")
        logger.debug(f"Payload for debugging: {json.dumps(body, ensure_ascii=False)}")
        return ConversionResponse(
            success=True,
            message="Label event received but label name not found in payload",
        )

    logger.info(f"🏷️ Label detected: \"{label_name}\"")

    # ── 6. Check if it matches any configured tag ──────────────────────
    tag_map = settings.get_tag_map()
    tag_key = label_name.strip().lower()

    if tag_key not in tag_map:
        logger.info(
            f"⏭️ Label \"{label_name}\" not in tag map {list(tag_map.keys())} — Skipping"
        )
        return ConversionResponse(
            success=True,
            message=f"Label '{label_name}' not configured for conversion tracking",
        )

    # Get the Meta event name for this tag
    meta_event_name = tag_map[tag_key]
    logger.info(f"🎯 Tag matched! \"{label_name}\" → Meta Event: \"{meta_event_name}\"")

    # ── 7. Extract phone number ────────────────────────────────────────
    phone = _extract_phone(body)

    if not phone:
        logger.error("❌ Label matched but couldn't extract phone number from payload")
        return ConversionResponse(
            success=False,
            message="Phone number not found in payload",
        )

    normalized_phone = normalize_phone(phone)
    phone_hash = hash_sha256(normalized_phone)

    logger.info(f"📱 Phone: {normalized_phone[:6]}***{normalized_phone[-2:]} → Hash: {phone_hash[:16]}...")

    # ── 8. Look up fbclid for precise attribution ──────────────────────
    fbclid = await supabase_logger.find_fbclid_by_phone(normalized_phone)
    lead_id = await supabase_logger.find_lead_id_by_phone(normalized_phone)

    if fbclid:
        logger.info(f"🔗 fbclid found — precise attribution enabled")
    else:
        logger.info(f"🔗 No fbclid found — using phone-only attribution")

    # ── 9. Fire conversion event to Meta CAPI ──────────────────────────
    logger.info(f"🔥 FIRING CONVERSION → Meta CAPI: {meta_event_name}")

    meta_result = await meta_capi_client.send_event(
        phone=normalized_phone,
        event_name=meta_event_name,
        value=settings.conversion_default_value,
        currency=settings.conversion_currency,
        fbclid=fbclid,
    )

    # ── 10. Log to Supabase for audit ──────────────────────────────────
    status = "sent" if meta_result.get("success") else "error"
    error_msg = meta_result.get("error") if not meta_result.get("success") else None

    await supabase_logger.log_conversion(
        phone_hash=phone_hash,
        event_name=meta_event_name,
        value=settings.conversion_default_value,
        currency=settings.conversion_currency,
        tag_name=label_name,
        meta_response=meta_result.get("response", {}),
        fbclid=fbclid,
        lead_id=lead_id,
        status=status,
        error_message=error_msg,
    )

    # ── 11. Return response ────────────────────────────────────────────
    if meta_result.get("success"):
        logger.info(f"✅ CONVERSION SENT SUCCESSFULLY — {meta_event_name} for {normalized_phone[:6]}***")
    else:
        logger.error(f"❌ CONVERSION FAILED — {meta_result.get('error')}")

    return ConversionResponse(
        success=meta_result.get("success", False),
        message=f"Conversion {'sent' if meta_result.get('success') else 'failed'} for tag '{label_name}'",
        event_name=meta_event_name,
        phone_hash=phone_hash,
        meta_response=meta_result,
    )


# ==========================================================================
# MANUAL TRIGGER (for testing)
# ==========================================================================
class ManualConversionRequest(BaseModel):
    phone: str
    event_name: Optional[str] = None
    value: Optional[float] = None
    test_event_code: Optional[str] = None


@app.post("/api/conversions/send")
async def manual_send_conversion(req: ManualConversionRequest):
    """
    Manually trigger a conversion event (for testing).

    Use the test_event_code from Meta Events Manager to test
    without affecting real campaign data.
    """
    settings = get_settings()

    normalized = normalize_phone(req.phone)
    fbclid = await supabase_logger.find_fbclid_by_phone(normalized)

    result = await meta_capi_client.send_event(
        phone=normalized,
        event_name=req.event_name or settings.conversion_event_name,
        value=req.value if req.value is not None else settings.conversion_default_value,
        currency=settings.conversion_currency,
        fbclid=fbclid,
        test_event_code=req.test_event_code,
    )

    return {
        "success": result.get("success"),
        "phone_normalized": f"{normalized[:6]}***",
        "event_name": req.event_name or settings.conversion_event_name,
        "fbclid_found": fbclid is not None,
        "meta_response": result,
    }


# ==========================================================================
# AUDIT LOGS
# ==========================================================================
@app.get("/api/conversions/logs")
async def get_conversion_logs(limit: int = 50):
    """List recent conversion event logs for audit."""
    logs = await supabase_logger.get_recent_logs(limit=limit)
    return {"total": len(logs), "logs": logs}


# ==========================================================================
# HELPER FUNCTIONS — Payload extraction
# ==========================================================================
def _extract_label_name(payload: Dict[str, Any]) -> Optional[str]:
    """
    Extract the label/tag name from various Evolution API payload formats.

    Evolution API can send label data in many different structures
    depending on the version. This function handles all known formats.
    """
    # Direct fields
    for key in ["labelName", "tagName", "label_name", "tag_name"]:
        if key in payload:
            return payload[key]

    # Nested in data
    data = payload.get("data", {})
    if isinstance(data, dict):
        for key in ["labelName", "tagName", "label_name", "tag_name", "label", "tag"]:
            val = data.get(key)
            if isinstance(val, str):
                return val

        # Label object with name
        label_obj = data.get("label") or data.get("tag")
        if isinstance(label_obj, dict):
            return label_obj.get("name") or label_obj.get("labelName")

        # Labels array
        labels = data.get("labels") or data.get("tags")
        if isinstance(labels, list) and len(labels) > 0:
            first = labels[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict):
                return first.get("name") or first.get("labelName")

    # Nested deeper (some versions use data.label.name)
    nested = payload.get("data", {}).get("data", {})
    if isinstance(nested, dict):
        for key in ["labelName", "tagName", "label", "tag"]:
            val = nested.get(key)
            if isinstance(val, str):
                return val

    return None


def _extract_phone(payload: Dict[str, Any]) -> Optional[str]:
    """
    Extract the phone number from various Evolution API payload formats.

    Handles:
    - remoteJid format (5511999998888@s.whatsapp.net)
    - Direct phone fields
    - Nested contact data
    - 'destination' field (Evolution API v2)
    - 'sender' field
    """
    data = payload.get("data", payload)

    # Direct phone fields
    for key in ["phone", "number", "remoteJid", "jid", "chatId", "wuid"]:
        val = data.get(key)
        if val and isinstance(val, str):
            return val

    # Check top-level 'destination' and 'sender' (Evolution API v2 format)
    for key in ["destination", "sender"]:
        val = payload.get(key)
        if val and isinstance(val, str):
            return val

    # Nested in key object
    key_obj = data.get("key", {})
    if isinstance(key_obj, dict):
        jid = key_obj.get("remoteJid") or key_obj.get("id")
        if jid:
            return jid

    # Nested in contact
    contact = data.get("contact", {})
    if isinstance(contact, dict):
        phone = contact.get("phone") or contact.get("number") or contact.get("id")
        if phone:
            return phone

    # Try nested data.data
    nested = data.get("data", {})
    if isinstance(nested, dict):
        for key in ["phone", "number", "remoteJid", "chatId"]:
            val = nested.get(key)
            if val:
                return val

    return None


# ==========================================================================
# ENTRYPOINT
# ==========================================================================
if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    port = int(os.environ.get("PORT", settings.port))

    logger.info(f"🚀 Starting Entony on {settings.host}:{port}")

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=port,
        reload=True,
    )
