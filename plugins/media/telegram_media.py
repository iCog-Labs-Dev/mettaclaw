import json
import os
import threading
import time
import urllib.parse
import urllib.request
import auth
from src.logger import get_logger
import pluginapi as plugin
import media_handler

logger = get_logger(__name__)

_running = False
_last_message = ""
_msg_lock = threading.Lock()
_state_lock = threading.Lock()

_bot_token = ""
_api_base = ""
_chat_id = ""
_poll_timeout = 20
_offset = None
_connected = False

_authenticated_user_id = None


def _set_last(msg):
    global _last_message
    with _msg_lock:
        if _last_message == "":
            _last_message = msg
        else:
            _last_message = _last_message + " | " + msg


def getLastMessage():
    global _last_message
    with _msg_lock:
        tmp = _last_message
        _last_message = ""
        return tmp


def _parse_auth_candidate(msg):
    text = msg.strip()
    lower = text.lower()
    if lower.startswith("auth "):
        return text[5:].strip()
    if lower.startswith("/auth "):
        return text[6:].strip()
    return text


def _display_name(user, chat):
    username = str(user.get("username", "")).strip()
    if username:
        return f"@{username}"

    first = str(user.get("first_name", "")).strip()
    last = str(user.get("last_name", "")).strip()
    full = f"{first} {last}".strip()
    if full:
        return full

    title = str(chat.get("title", "")).strip()
    if title:
        return title

    return "telegram_user"


def _api_call(method, params=None, timeout=30, use_post=False):
    if not _api_base:
        raise RuntimeError("Telegram adapter not initialized")

    params = params or {}
    encoded = urllib.parse.urlencode(params).encode("utf-8")
    url = f"{_api_base}/{method}"

    if use_post:
        req = urllib.request.Request(url, data=encoded)
    else:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url)

    with urllib.request.urlopen(req, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="ignore"))

    if not payload.get("ok"):
        raise RuntimeError(payload.get("description", f"{method} failed"))

    return payload.get("result")


def _download_file(file_id):
    """Resolve a Telegram file_id to raw bytes via getFile + the file download
    endpoint. Proxy mode derives a best-effort file base from _api_base since
    the proxy's file-serving path is untested."""
    result = _api_call("getFile", {"file_id": file_id}, timeout=30)
    file_path = result["file_path"]

    if _bot_token == "proxy":
        # Best-effort: proxy file download is untested.
        url = f"{_api_base}/file/{file_path}"
    else:
        url = f"https://api.telegram.org/file/bot{_bot_token}/{file_path}"

    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def _ingest_media(message):
    """Route a text-less message to the right media_handler call and return the
    display text to feed into the normal _set_last flow, or None if the message
    type is unsupported. Never raises — a download/decode failure is caught per
    branch and turned into a plain marker so the turn is not broken."""
    caption = str(message.get("caption") or "").strip()

    photo = message.get("photo")
    if isinstance(photo, list) and photo:
        try:
            file_id = photo[-1].get("file_id")
            raw = _download_file(file_id)
            img = media_handler.sanitize_image(raw)
            uri = media_handler.image_to_data_uri(img, "image/jpeg")
            media_handler.set_pending_media([{"type": "image_url", "image_url": {"url": uri}}])
            return f"[image] {caption}".rstrip()
        except Exception as exc:
            logger.warning(f"Photo ingestion failed: {exc}")
            return "[image could not be processed]"

    document = message.get("document")
    if isinstance(document, dict):
        if document.get("mime_type") != "application/pdf":
            return None
        try:
            file_id = document.get("file_id")
            file_name = document.get("file_name") or "document.pdf"
            raw = _download_file(file_id)
            text = media_handler.extract_pdf_text(raw, file_name)
            return f"[uploaded PDF '{file_name}'] — {caption or 'please summarize this PDF'}\n{text}"
        except Exception as exc:
            logger.warning(f"PDF ingestion failed: {exc}")
            return "[file could not be processed]"

    voice = message.get("voice") or message.get("audio")
    if isinstance(voice, dict):
        try:
            file_id = voice.get("file_id")
            file_name = voice.get("file_name") or "voice.ogg"
            raw = _download_file(file_id)
            transcript = media_handler.transcribe_audio(raw, file_name)
            return f"[voice message] {transcript}"
        except Exception as exc:
            logger.warning(f"Voice ingestion failed: {exc}")
            return "[file could not be processed]"

    return None


def _initialize_offset():
    global _offset
    try:
        updates = _api_call("getUpdates", {"timeout": 0}, timeout=10) or []
    except Exception as exc:
        logger.warning(f"Could not read initial offset: {exc}")
        return

    max_update = -1
    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            max_update = max(max_update, update_id)

    if max_update >= 0:
        with _state_lock:
            _offset = max_update + 1


def _is_auth_command(msg):
    lower = msg.strip().lower()
    return lower.startswith("auth ") or lower.startswith("/auth ")


def _is_allowed_message(chat_id, user_id, msg):
    global _chat_id, _authenticated_user_id

    with _state_lock:
        if _chat_id and chat_id != _chat_id:
            return "ignore"
        if not auth.is_auth_enabled():
            if not _chat_id:
                _chat_id = chat_id
            return "allow"
        if _authenticated_user_id is not None:
            if chat_id != _chat_id:
                return "ignore"
            return "allow" if user_id == _authenticated_user_id else "ignore"
        candidate = _parse_auth_candidate(msg)
        user_id_check = auth.authenticate_channel_user('TELEGRAM', user_id, candidate)
        if user_id_check in ["auth_bound", "allow"]:
            _authenticated_user_id = user_id
            _chat_id = chat_id
            return user_id_check
        else:
            return "ignore"


def _poll_loop():
    global _connected, _offset
    logger.info("Polling started")

    while _running:
        try:
            params = {"timeout": int(_poll_timeout)}
            with _state_lock:
                if _offset is not None:
                    params["offset"] = _offset

            updates = _api_call("getUpdates", params=params, timeout=int(_poll_timeout) + 10) or []
            _connected = True

            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    with _state_lock:
                        if _offset is None or (update_id + 1) > _offset:
                            _offset = update_id + 1

                message = update.get("message") or update.get("edited_message")
                if not isinstance(message, dict):
                    continue

                chat = message.get("chat") or {}
                user = message.get("from") or {}
                chat_id = str(chat.get("id", "")).strip()
                user_id = str(user.get("id", "")).strip()
                if not chat_id or not user_id:
                    continue

                text = message.get("text")
                state = _is_allowed_message(chat_id, user_id, text or "")
                display_name = _display_name(user, chat)

                if state == "allow":
                    if not text:
                        text = _ingest_media(message)
                        if text is None:
                            continue
                    _set_last(f"{display_name}: {text}")
                elif state == "auth_bound":
                    send_message(f"Authentication successful for {display_name}.")
        except Exception as exc:
            _connected = False
            logger.warning(f"Poll error: {exc}")
            time.sleep(2)

    _connected = False
    logger.info("Polling stopped")


def start_telegram(chat_id="", poll_timeout=20):
    global _running, _bot_token, _api_base, _chat_id, _poll_timeout, _offset, _connected

    proxy = auth.get_proxy_url()
    if proxy:
        _bot_token = "proxy"
        _api_base = f"{proxy}/telegram"
    else:
        _bot_token = os.environ.get("TG_BOT_TOKEN", "").strip()
        if not _bot_token:
            raise ValueError("TG_BOT_TOKEN is required")
        _api_base = f"https://api.telegram.org/bot{_bot_token}"

    _chat_id = str(chat_id).strip()

    try:
        _poll_timeout = max(1, int(poll_timeout))
    except Exception as e:
        logger.warning(f"Invalid poll_timeout {poll_timeout!r}, falling back to 20: {e}")
        _poll_timeout = 20

    _offset = None
    _running = True
    _connected = False
    logger.info(f"Starting adapter with chat target: {_chat_id or 'auto-bind'}")
    _initialize_offset()

    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()
    return t


def stop_telegram():
    global _running
    _running = False


def send_message(text):
    text = str(text).replace("\\n", "\n").replace("\r", "")
    if not text:
        return

    with _state_lock:
        target_chat = _chat_id

    if not _connected or not target_chat:
        return

    max_len = 3900
    for i in range(0, len(text), max_len):
        chunk = text[i:i + max_len]
        if not chunk:
            continue
        try:
            _api_call(
                "sendMessage",
                {"chat_id": target_chat, "text": chunk},
                timeout=15,
                use_post=True,
            )
        except Exception as exc:
            logger.exception(f"Send failed: {exc}")
            return

class TelegramChannel(plugin.CommChannel):

    def __init__(self):
        super().__init__()

    def config(self, config: dict) -> None:
        chat_id = config.get("TG_CHAT_ID", "")
        poll_timeout = int(config.get("TG_POLL_TIMEOUT", 20))
        start_telegram(chat_id, poll_timeout)

    def receive(self) -> str:
        return getLastMessage()

    def send(self, message: str) -> None:
        send_message(message)

def loadOmegaClawPlugin():
    plugin.registerCommChannel("telegram_media", TelegramChannel())
