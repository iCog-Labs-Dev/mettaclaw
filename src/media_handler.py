import base64
import threading
import logging
import sys

logger = logging.getLogger(__name__)

# This module is reached under two import names: `media_handler` (MeTTa py-calls
# in skills.metta, e.g. describe-image / generate-image) and `src.media_handler`
# (tg_channel, lib_llm_ext). Without this, each name gets its OWN module object
# with separate globals, so pending media set via one name is invisible to
# describe_image called via the other. Alias them to a single instance.
_self = sys.modules[__name__]
sys.modules.setdefault("media_handler", _self)
sys.modules.setdefault("src.media_handler", _self)

_lock = threading.Lock()
_pending_media = None
_pending_context = None
_describe_media = None  # image kept for describe-image; survives a reply's clear_pending()
_pending_description = {}  # (image_key, query) -> caption marker, per-turn memo


def set_pending_media(media):
    global _pending_media, _describe_media
    with _lock:
        _pending_media = media
        imgs = _image_parts(media)
        if imgs:
            # New image arrived: keep an independent copy for describe-image so a
            # reply's clear_pending() can't blind it mid-turn, and drop stale captions.
            _describe_media = media
            _pending_description.clear()
            logger.info("[IMGDBG] pending media set: %d image part(s)", len(imgs))
        elif media is None:
            # A new non-image message was consumed -> the prior image is now stale.
            _describe_media = None
    # ponytail: describe source holds one base64 image until the next message; bounded, overwritten.


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
    """Drop both out-of-band slots and the image-description memo once the
    agent has replied to the user."""
    global _pending_media, _pending_context
    with _lock:
        had = bool(_pending_media) or bool(_pending_context)
        _pending_media = None
        _pending_context = None
        _pending_description.clear()
    if had:
        logger.info("[IMGDBG] clear_pending: dropped main media/context (describe source kept)")


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


import hashlib

VISION_PROMPT = (
    "Describe this image for a text-only assistant. Report objects, any visible "
    "text verbatim, layout, and notable details. Be concise and factual."
)


def _image_parts(media):
    """The image_url parts of a pending-media list (empty if none)."""
    return [p for p in (media or [])
            if isinstance(p, dict) and p.get("type") == "image_url"]


def _image_key(image_parts):
    """Cheap identity for the pending image(s) — hash of the data URI(s)."""
    urls = "".join(
        (p.get("image_url") or {}).get("url", "") for p in image_parts
    )
    return hashlib.sha256(urls.encode("utf-8")).hexdigest()


def _call_vision_model(image_parts, prompt, model):
    """Raw vision chat call via Anthropic's OpenAI-compatible endpoint (the main
    agent stays on its own provider; only image reads go to Claude). Returns
    caption text; raises on failure. Isolated so tests can stub it."""
    import os
    import openai
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    client = openai.OpenAI(api_key=api_key, base_url="https://api.anthropic.com/v1/")
    content = [{"type": "text", "text": prompt}] + image_parts
    resp = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": content}]
    )
    return (resp.choices[0].message.content or "").strip()


def describe_image(query=""):
    """MeTTa skill entry point (describe-image): caption the currently-pending
    image with a vision model so a non-vision agent can 'see' it. Optional
    `query` focuses the description. Memoized per turn; never raises."""
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

    try:
        import os
        model = os.environ.get("VISION_MODEL", "claude-haiku-4-5")
        prompt = VISION_PROMPT if not query else f"{VISION_PROMPT} Focus on: {query}"
        caption = _call_vision_model(parts, prompt, model)
        result = f"[IMAGE DESCRIPTION]\n{caption}"
        with _lock:
            _pending_description[key] = result
        logger.info(f"Described pending image via {model}: {len(caption)} chars")
        return result
    except Exception as e:
        logger.error(f"Image description failed: {e}")
        return f"[IMAGE_DESCRIPTION_FAILED: {e}]"


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
            resp = client.images.generate(model=model, prompt=prompt)
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


def test_describe_image():
    global _call_vision_model
    # 1. No image pending -> NO_IMAGE marker
    clear_pending()
    set_pending_media(None)
    assert describe_image("anything") == "[NO_IMAGE: nothing is attached to describe]"

    # 2. Memo: second identical call is served from cache (no 2nd API call)
    calls = {"n": 0}
    orig = _call_vision_model

    def fake(image_parts, prompt, model):
        calls["n"] += 1
        return "a red panda"

    _call_vision_model = fake
    try:
        set_pending_media([{"type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64,AAAA"}}])
        r1 = describe_image("")
        r2 = describe_image("")
        assert r1 == r2 == "[IMAGE DESCRIPTION]\na red panda", r1
        assert calls["n"] == 1, calls["n"]

        # 3. Race fix: a reply's clear_pending() must NOT blind describe-image.
        #    The image stays describable until a new (non-image) message arrives.
        clear_pending()  # simulates send_message() replying to the user
        assert describe_image("") == "[IMAGE DESCRIPTION]\na red panda"
        set_pending_media(None)  # a new text message is consumed -> image now stale
        assert describe_image("") == "[NO_IMAGE: nothing is attached to describe]"
    finally:
        _call_vision_model = orig
        clear_pending()
        set_pending_media(None)
    print("test_describe_image passed")


if __name__ == "__main__":
    test_describe_image()
