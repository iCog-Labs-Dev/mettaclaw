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


def sanitize_image(file_bytes, max_dim=2048, quality=85):
    """Re-encode an image via Pillow: strips EXIF/metadata and destroys most
    LSB-embedded steganographic payloads. Raises on undecodable input so the
    caller's existing error path rejects the file."""
    from PIL import Image
    from io import BytesIO
    img = Image.open(BytesIO(file_bytes))
    img = img.convert("RGB")
    if max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim))
    out = BytesIO()
    img.save(out, format="JPEG", quality=quality)
    return out.getvalue()


def extract_pdf_text(file_bytes, filename, max_chars=20000):
    try:
        from pypdf import PdfReader
        from io import BytesIO

        reader = PdfReader(BytesIO(file_bytes))
        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        text = "\n".join(pages)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n[truncated]"
        logger.info(f"Extracted text from {filename}: {len(text)}")
        return f"[PDF: {filename}]\n{text}"
    except Exception as e:
        logger.error(f"PDF extraction failed for {filename}: {e}")
        return f"[PDF: {filename}]\n[Could not extract text: {e}]"


def transcribe_audio(file_bytes, filename, model="openai/whisper-large-v3", max_bytes=25 * 1024 * 1024, language=None, temperature=None):
    """Transcribe an audio/voice file to text via OpenRouter Whisper Large V3.

    Returns a marker-prefixed string for the PDF-style context slot, or an
    error note (never raises) so the agent still gets a usable turn.
    """
    try:
        if len(file_bytes) > max_bytes:
            return f"[AUDIO TRANSCRIPT: {filename}]\n[Audio too large to transcribe]"

        import os, io, openai

        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            return f"[AUDIO TRANSCRIPT: {filename}]\n[Could not transcribe: OPENROUTER_API_KEY is not set]"

        client = openai.OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1",)
        audio_file = io.BytesIO(file_bytes)
        audio_file.name = filename
        kwargs = { "model": model, "file": audio_file,}
        if language:
            kwargs["language"] = language

        if temperature is not None:
            kwargs["temperature"] = temperature

        resp = client.audio.transcriptions.create(**kwargs)
        text = (getattr(resp, "text", "") or "").strip()
        logger.info(f"Transcribed {filename}: {len(text)} chars")

        return f"[AUDIO TRANSCRIPT: {filename}]\n{text}"

    except Exception as e:
        logger.error(f"Audio transcription failed for {filename}: {e}")
        return f"[AUDIO TRANSCRIPT: {filename}]\n[Could not transcribe: {e}]"
