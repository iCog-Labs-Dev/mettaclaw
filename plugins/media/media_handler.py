import base64
import hashlib
import threading
import logging
import sys

logger = logging.getLogger(__name__)

# skills.metta reaches this via `py-call (media_handler.describe_image ...)`, so
# the module must resolve under the bare name `media_handler` even when Python
# first imported it under a package-qualified name. Alias to one instance.
_self = sys.modules[__name__]
sys.modules.setdefault("media_handler", _self)

_lock = threading.Lock()
_pending_media = None
_describe_media = None          # kept for describe-image; survives a reply's clear_pending()
_pending_description = {}       # (image_key, query) -> caption, per-turn memo

VISION_PROMPT = (
    "Describe this image for a text-only assistant. Report objects, any visible "
    "text verbatim, layout, and notable details. Be concise and factual."
)


def set_pending_media(media):
    global _pending_media, _describe_media
    with _lock:
        _pending_media = media
        imgs = _image_parts(media)
        if imgs:
            _describe_media = media
            _pending_description.clear()
            logger.info("[IMGDBG] pending media set: %d image part(s)", len(imgs))
        elif media is None:
            _describe_media = None


def get_pending_media():
    with _lock:
        return _pending_media


def clear_pending():
    """Drop the pending slot and memo once the agent has replied. The describe
    source is kept so describe-image is not blinded mid-turn by a reply."""
    global _pending_media
    with _lock:
        had = bool(_pending_media)
        _pending_media = None
        _pending_description.clear()
    if had:
        logger.info("[IMGDBG] clear_pending: dropped pending media (describe source kept)")


def _image_parts(media):
    return [p for p in (media or [])
            if isinstance(p, dict) and p.get("type") == "image_url"]


def _image_key(image_parts):
    urls = "".join((p.get("image_url") or {}).get("url", "") for p in image_parts)
    return hashlib.sha256(urls.encode("utf-8")).hexdigest()


def _call_vision_model(image_parts, prompt):
    """Vision call via the OpenRouterVision provider. Isolated so tests stub it."""
    from openrouter_vision import vision_chat
    return vision_chat(image_parts, prompt)


def describe_image(query=""):
    """describe-image skill: caption the currently-pending image so a non-vision
    agent can 'see' it. Memoized per turn; never raises — any failure, including
    a malformed pending-media value from a producer, returns a failure marker."""
    try:
        query = (query or "").strip()
        with _lock:
            source = _pending_media if _image_parts(_pending_media) else _describe_media
        parts = _image_parts(source)
        if not parts:
            logger.info("[IMGDBG] describe_image: no pending image available")
            return "[NO_IMAGE: nothing is attached to describe]"

        key = (_image_key(parts), query)
        with _lock:
            cached = _pending_description.get(key)
        if cached is not None:
            return cached

        prompt = VISION_PROMPT if not query else f"{VISION_PROMPT} Focus on: {query}"
        caption = _call_vision_model(parts, prompt)
        result = f"[IMAGE DESCRIPTION]\n{caption}"
        with _lock:
            _pending_description[key] = result
        logger.info("Described pending image: %d chars", len(caption))
        return result
    except Exception as e:
        logger.error("Image description failed: %s", e)
        return f"[IMAGE_DESCRIPTION_FAILED: {e}]"


def image_to_data_uri(file_bytes, mime_type):
    """Helper for image ingestion (used by the channel plugin later)."""
    encoded = base64.b64encode(file_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"
