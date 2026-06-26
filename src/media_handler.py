import base64
import threading
import logging

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_pending_media = None


def set_pending_media(media):
    global _pending_media
    with _lock:
        _pending_media = media


def get_and_clear_pending_media():
    global _pending_media
    with _lock:
        media = _pending_media
        _pending_media = None
        return media


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
        return f"[PDF: {filename}]\n{text}"
    except Exception as e:
        logger.error(f"PDF extraction failed for {filename}: {e}")
        return f"[PDF: {filename}]\n[Could not extract text: {e}]"


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
