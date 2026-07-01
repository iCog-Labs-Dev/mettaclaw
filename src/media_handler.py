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


def transcribe_audio(file_bytes, filename, model="whisper-1", max_bytes=25 * 1024 * 1024):
    """Transcribe an audio/voice file to text via the OpenAI Whisper API.
    Returns a marker-prefixed string for the PDF-style context slot, or an
    error note (never raises) so the agent still gets a usable turn."""
    try:
        if len(file_bytes) > max_bytes:
            return f"[AUDIO TRANSCRIPT: {filename}]\n[Audio too large to transcribe]"
        import os, openai
        client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        resp = client.audio.transcriptions.create(model=model, file=(filename, file_bytes))
        text = (resp.text or "").strip()
        logger.info(f"Transcribed {filename}: {len(text)} chars")
        return f"[AUDIO TRANSCRIPT: {filename}]\n{text}"
    except Exception as e:
        logger.error(f"Audio transcription failed for {filename}: {e}")
        return f"[AUDIO TRANSCRIPT: {filename}]\n[Could not transcribe: {e}]"


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
