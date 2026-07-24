import os
import logging

logger = logging.getLogger(__name__)

_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_MODEL = "anthropic/claude-haiku-4.5"


def _model():
    return os.environ.get("VISION_MODEL", _DEFAULT_MODEL)


def _make_client():
    """Create the OpenRouter client. Isolated so tests can stub it."""
    import openai
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OpenRouterVision not available (set OPENROUTER_API_KEY)")
    return openai.OpenAI(api_key=api_key, base_url=_BASE_URL)


def vision_chat(image_parts, prompt):
    """Caption image(s) via an OpenRouter vision model. `image_parts` are OpenAI
    multimodal image_url parts. Returns the caption; raises on failure."""
    client = _make_client()
    content = [{"type": "text", "text": prompt}] + list(image_parts)
    resp = client.chat.completions.create(
        model=_model(),
        messages=[{"role": "user", "content": content}],
    )
    caption = (resp.choices[0].message.content or "").strip()
    if not caption:
        raise RuntimeError("empty caption from vision provider")
    logger.info("OpenRouterVision described image: %d chars", len(caption))
    return caption


def loadOmegaClawPlugin():
    """Plugin entry point. Importing this module puts `plugins/media` on sys.path
    and imports media_handler, so `py-call (media_handler.describe_image ...)` in
    skills.metta resolves. describe_image calls vision_chat directly, so no
    LLMProvider needs registering for the describe-image path."""
    import media_handler  # noqa: F401 — ensures the media_handler alias is set
