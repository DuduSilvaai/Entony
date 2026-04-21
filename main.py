"""
Entony — Webhook Listener (Stateless)
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
    version="2.1.0",
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


# ==========================================================================
# STARTUP
# ==========================================================================
@app.on_event("startup")
async def startup():
    settings = get_settings()
    tag_map = settings.get_tag_map()

    logger.info("=" * 60)
    logger.info("🚀 ENTONY v2.1 (Stateless) — Webhook Listener starting up...")
    logger.info(f"   📡 Meta Pixel ID: {settings.meta_pixel_id}")
    logger.info(f"   🏷️  Tag Map: {json.dumps(tag_map, ensure_ascii=False)}")
    logger.info(f"   💰 Default Value: {settings.conversion_currency} {settings.conversion_default_value}")
    logger.info(f"   🔑 API Key configured: {'Yes' if settings.evolution_api_key else 'No (open)'}")
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
        "version": "2.1.0",
        "meta_pixel_id": settings.meta_pixel_id,
        "tag_map": settings.get_tag_map(),
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
    """
    settings = get_settings()

    # ── 1. Parse raw body ──────────────────────────────────────────────
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Log raw payload for debugging
    logger.info(f"📨 Webhook received — Keys: {list(body.keys())}")

    # ── 2. Validate API Key ────────────────────────────────────────────
    if settings.evolution_api_key:
        incoming_key = (
            request.headers.get("apikey")
            or request.headers.get("x-api-key")
            or request.headers.get("authorization", "").replace("Bearer ", "").strip()
        )

        if not incoming_key and isinstance(body, dict):
            incoming_key = body.get("apikey", "")

        if not incoming_key:
            incoming_key = request.query_params.get("apikey", "")

        incoming_key = (incoming_key or "").strip()

        if incoming_key != settings.evolution_api_key:
            logger.warning(f"⛔ Unauthorized webhook attempt — Key mismatch")
            raise HTTPException(status_code=401, detail="Invalid API key")

    # ── 3. Detect event type ───────────────────────────────────────────
    event_type = (
        body.get("event")
        or body.get("type")
        or body.get("action")
        or ""
    ).lower()

    # ── 4. Filter for label events ─────────────────────────────────────
    data = body.get("data", body)
    has_label_data = any(
        key in data for key in ["label", "labels", "tag", "tags", "labelName", "tagName"]
    )

    if "label" not in event_type and "tag" not in event_type and not has_label_data:
        logger.info(f"⏭️ Ignoring non-label event: \"{event_type}\"")
        return ConversionResponse(
            success=True,
            message=f"Event ignored — not a label event",
        )

    # ── 5. Extract label name ──────────────────────────────────────────
    label_name = _extract_label_name(body)
    if not label_name:
        return ConversionResponse(success=True, message="Label name not found in payload")

    # ── 6. Check if it matches any configured tag ──────────────────────
    tag_map = settings.get_tag_map()
    tag_key = label_name.strip().lower()

    if tag_key not in tag_map:
        logger.info(f"⏭️ Label \"{label_name}\" not configured — Skipping")
        return ConversionResponse(success=True, message=f"Label '{label_name}' not tracked")

    meta_event_name = tag_map[tag_key]

    # ── 7. Extract phone number ────────────────────────────────────────
    phone = _extract_phone(body)
    if not phone:
        return ConversionResponse(success=False, message="Phone number not found")

    normalized_phone = normalize_phone(phone)
    phone_hash = hash_sha256(normalized_phone)

    # ── 8. Fire conversion event to Meta CAPI ──────────────────────────
    logger.info(f"🔥 FIRING CONVERSION → Meta CAPI: {meta_event_name}")

    meta_result = await meta_capi_client.send_event(
        phone=normalized_phone,
        event_name=meta_event_name,
        value=settings.conversion_default_value,
        currency=settings.conversion_currency,
        fbclid=None, # Stateless: no DB lookup for fbclid
    )

    # ── 9. Return response ────────────────────────────────────────────
    if meta_result.get("success"):
        logger.info(f"✅ SENT SUCCESSFULLY — {meta_event_name} for {normalized_phone[:6]}***")
    else:
        logger.error(f"❌ FAILED — {meta_result.get('error')}")

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
    settings = get_settings()
    normalized = normalize_phone(req.phone)

    result = await meta_capi_client.send_event(
        phone=normalized,
        event_name=req.event_name or settings.conversion_event_name,
        value=req.value if req.value is not None else settings.conversion_default_value,
        currency=settings.conversion_currency,
        fbclid=None,
        test_event_code=req.test_event_code,
    )

    return {
        "success": result.get("success"),
        "event_name": req.event_name or settings.conversion_event_name,
        "meta_response": result,
    }


# ==========================================================================
# HELPER FUNCTIONS — Payload extraction
# ==========================================================================
def _extract_label_name(payload: Dict[str, Any]) -> Optional[str]:
    for key in ["labelName", "tagName", "label_name", "tag_name"]:
        if key in payload: return payload[key]
    
    data = payload.get("data", {})
    if isinstance(data, dict):
        for key in ["labelName", "tagName", "label_name", "tag_name", "label", "tag"]:
            val = data.get(key)
            if isinstance(val, str): return val
        
        label_obj = data.get("label") or data.get("tag")
        if isinstance(label_obj, dict): return label_obj.get("name") or label_obj.get("labelName")

        labels = data.get("labels") or data.get("tags")
        if isinstance(labels, list) and len(labels) > 0:
            first = labels[0]
            if isinstance(first, str): return first
            if isinstance(first, dict): return first.get("name") or first.get("labelName")
    return None


def _extract_phone(payload: Dict[str, Any]) -> Optional[str]:
    data = payload.get("data", payload)
    for key in ["phone", "number", "remoteJid", "jid", "chatId", "wuid"]:
        val = data.get(key)
        if val and isinstance(val, str): return val

    for key in ["destination", "sender"]:
        val = payload.get(key)
        if val and isinstance(val, str): return val

    key_obj = data.get("key", {})
    if isinstance(key_obj, dict):
        jid = key_obj.get("remoteJid") or key_obj.get("id")
        if jid: return jid

    contact = data.get("contact", {})
    if isinstance(contact, dict):
        phone = contact.get("phone") or contact.get("number") or contact.get("id")
        if phone: return phone

    return None


# ==========================================================================
# ENTRYPOINT
# ==========================================================================
if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    port = int(os.environ.get("PORT", settings.port))
    uvicorn.run("main:app", host=settings.host, port=port, reload=True)
