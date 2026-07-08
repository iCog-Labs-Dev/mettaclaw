import base64
import threading
import logging

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_pending_media = None
_pending_context = None


def set_pending_media(media):
    global _pending_media
    with _lock:
        _pending_media = media


def get_pending_media():
    with _lock:
        return _pending_media


def set_pending_context(text):
    global _pending_context
    with _lock:
        _pending_context = text


def get_pending_context():
    with _lock:
        return _pending_context


def clear_pending():
    """Drop both out-of-band slots once the agent has replied to the user."""
    global _pending_media, _pending_context
    with _lock:
        _pending_media = None
        _pending_context = None


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


def image_to_data_uri(file_bytes, mime_type):
    encoded = base64.b64encode(file_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


PROVIDER_VISION = {
    "Anthropic": True,
    "OpenAI": True,
    "OpenRouter": False,
    "ASIOne": False,
    "ASICloud": False,
    "Ollama-local": False,
}


def supports_vision(provider_name):
    return PROVIDER_VISION.get(provider_name, False)


def build_multimodal_content(text, media):
    return [{"type": "text", "text": text}] + media


# --- Image generation (outbound) -------------------------------------------
# Provider-agnostic text-to-image. The default is OpenRouter FLUX; the image
# provider is chosen INDEPENDENTLY of the chat `provider` (not every chat
# provider can generate images) via IMAGE_PROVIDER / IMAGE_MODEL env vars.
# OpenRouter's image endpoint is NOT OpenAI-SDK compatible (dedicated
# POST /api/v1/images), so that path uses a raw requests POST; both styles
# return the image as base64 in data[0].b64_json.
IMAGE_PROVIDERS = {
    "OpenRouter": {
        "style": "openrouter",
        "url": "https://openrouter.ai/api/v1/images",
        "key_env": "OPENROUTER_API_KEY",
        "default_model": "black-forest-labs/flux.2-pro",
    },
    "OpenAI": {
        "style": "openai_sdk",
        "base_url": "https://api.openai.com/v1",
        "key_env": "OPENAI_API_KEY",
        "default_model": "gpt-image-1",
    },
}


def _generate_image_bytes(prompt):
    """Generate an image for `prompt` via the configured image provider.
    Returns raw image bytes, or None on any failure (never raises)."""
    import os
    provider_name = os.environ.get("IMAGE_PROVIDER", "OpenRouter")
    cfg = IMAGE_PROVIDERS.get(provider_name) or IMAGE_PROVIDERS["OpenRouter"]
    model = os.environ.get("IMAGE_MODEL", cfg["default_model"])
    api_key = os.environ.get(cfg["key_env"])
    if not api_key:
        logger.error(f"Image generation: {cfg['key_env']} not set for provider {provider_name}")
        return None
    try:
        if cfg["style"] == "openrouter":
            import requests
            resp = requests.post(
                cfg["url"],
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={"model": model, "prompt": prompt},
                timeout=120,
            )
            resp.raise_for_status()
            b64 = resp.json()["data"][0]["b64_json"]
        elif cfg["style"] == "openai_sdk":
            import openai
            client = openai.OpenAI(api_key=api_key, base_url=cfg.get("base_url"))
            resp = client.images.generate(model=model, prompt=prompt, response_format="b64_json")
            b64 = resp.data[0].b64_json
        else:
            logger.error(f"Image generation: unknown style for provider {provider_name}")
            return None
        image_bytes = base64.b64decode(b64)
        logger.info(f"Generated image via {provider_name}/{model}: {len(image_bytes)} bytes")
        return image_bytes
    except Exception as e:
        logger.error(f"Image generation failed ({provider_name}/{model}): {e}")
        return None


def _live_tg_channel():
    """Return the tg_channel module whose _channel is the LIVE aiogram-connected
    instance. The bot is loaded by MeTTa as top-level `tg_channel`, but a plain
    `from channels import tg_channel` yields a SECOND, unconnected module object
    (channels/ has no __init__.py -> implicit namespace package -> a separate
    sys.modules entry with its own never-started _channel). Search sys.modules for
    the connected one so send_photo reaches the running bot; fall back gracefully."""
    import sys
    fallback = None
    for name, mod in list(sys.modules.items()):
        if mod is None or not (name == "tg_channel" or name.endswith(".tg_channel")):
            continue
        ch = getattr(mod, "_channel", None)
        if ch is not None and getattr(ch, "connected", False):
            return mod
        if fallback is None and ch is not None:
            fallback = mod
    if fallback is not None:
        return fallback
    from channels import tg_channel
    return tg_channel


def _image_generation_allowed():
    """Read the allow_image_generation gate from the active channel's reply
    constraints. Fail closed (False) if unavailable."""
    try:
        tg_channel = _live_tg_channel()
        constraints = getattr(tg_channel._channel, "reply_constraints", None) or {}
        return bool(constraints.get("allow_image_generation", False))
    except Exception as e:
        logger.error(f"Could not read allow_image_generation gate: {e}")
        return False


def _prompt_is_unsafe(prompt):
    """Run the ethics classifier on the image prompt before spending money on
    generation. Sync bridge over the async is_category_blocked, mirroring
    tg_channel.send_message. Fail closed (unsafe) on unexpected error."""
    import asyncio
    from src.config_helper import is_category_blocked
    try:
        try:
            loop = asyncio.get_running_loop()
            return loop.run_until_complete(is_category_blocked(prompt))
        except RuntimeError:
            return asyncio.run(is_category_blocked(prompt))
    except Exception as e:
        logger.error(f"Image prompt ethics check failed: {e}")
        return True


def generate_and_send(prompt):
    """MeTTa skill entry point (generate-image): generate an image for `prompt`
    and send it to the user. Returns a short status string (never raises) that
    the agent sees next cycle in LAST_SKILL_USE_RESULTS. Bytes are generated and
    dispatched here (not through MeTTa, which only handles strings)."""
    prompt = (prompt or "").strip()
    if not prompt:
        return "IMAGE_FAILED: empty prompt"
    if not _image_generation_allowed():
        return "IMAGE_DISABLED: image generation is turned off"
    if _prompt_is_unsafe(prompt):
        return "Refused: unsafe image prompt"
    image_bytes = _generate_image_bytes(prompt)
    if not image_bytes:
        return f"IMAGE_FAILED: could not generate image for: {prompt}"
    try:
        tg_channel = _live_tg_channel()
        tg_channel.send_photo(image_bytes, caption=prompt[:1024])
    except Exception as e:
        logger.error(f"Failed to send generated image: {e}")
        return f"IMAGE_FAILED: generated but could not send: {e}"
    return f"IMAGE_SENT: {prompt}"
