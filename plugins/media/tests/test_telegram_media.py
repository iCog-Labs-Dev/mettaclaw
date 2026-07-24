import os, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEDIA_DIR = os.path.dirname(_HERE)
_REPO_ROOT = os.path.dirname(os.path.dirname(_MEDIA_DIR))

# telegram_media.py pulls in auth (channels/), src.logger and pluginapi (src/),
# same as core's channels/telegram.py does when loaded as a real plugin.
sys.path.insert(0, _MEDIA_DIR)
sys.path.insert(0, os.path.join(_REPO_ROOT, "channels"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))
sys.path.insert(0, _REPO_ROOT)

import telegram_media as tm
import media_handler as mh


def _stub(monkeys):
    """Set module attributes, return a restore function."""
    saved = {}
    for mod, name, value in monkeys:
        saved[(mod, name)] = getattr(mod, name)
        setattr(mod, name, value)

    def restore():
        for (mod, name), value in saved.items():
            setattr(mod, name, value)
    return restore


def test_ingest_media_photo_returns_image_marker_and_sets_pending():
    calls = {"set_pending": None}
    restore = _stub([
        (tm, "_download_file", lambda file_id: b"raw-bytes"),
        (mh, "sanitize_image", lambda raw: b"jpeg-bytes"),
        (mh, "image_to_data_uri", lambda img, mime: "data:image/jpeg;base64,AAAA"),
        (mh, "set_pending_media", lambda media: calls.__setitem__("set_pending", media)),
    ])
    try:
        message = {
            "photo": [{"file_id": "small"}, {"file_id": "large"}],
            "caption": "look at this",
        }
        out = tm._ingest_media(message)
        assert out.startswith("[image]"), out
        assert "look at this" in out, out
        assert calls["set_pending"] is not None
        assert len(calls["set_pending"]) == 1
        assert calls["set_pending"][0]["type"] == "image_url"
    finally:
        restore()


def test_ingest_media_pdf_returns_filename_and_text():
    restore = _stub([
        (tm, "_download_file", lambda file_id: b"pdf-bytes"),
        (mh, "extract_pdf_text", lambda raw, filename: "extracted text here"),
    ])
    try:
        message = {
            "document": {"file_id": "f1", "mime_type": "application/pdf", "file_name": "report.pdf"},
            "caption": "",
        }
        out = tm._ingest_media(message)
        assert "report.pdf" in out, out
        assert "extracted text here" in out, out
    finally:
        restore()


def test_ingest_media_non_pdf_document_returns_none():
    message = {"document": {"file_id": "f1", "mime_type": "text/plain", "file_name": "notes.txt"}}
    assert tm._ingest_media(message) is None


def test_ingest_media_voice_returns_transcript():
    restore = _stub([
        (tm, "_download_file", lambda file_id: b"audio-bytes"),
        (mh, "transcribe_audio", lambda raw, filename: "hello from the transcript"),
    ])
    try:
        message = {"voice": {"file_id": "v1"}}
        out = tm._ingest_media(message)
        assert "[voice message]" in out, out
        assert "hello from the transcript" in out, out
    finally:
        restore()


def test_ingest_media_photo_download_failure_returns_marker():
    def boom(file_id):
        raise RuntimeError("network down")

    restore = _stub([(tm, "_download_file", boom)])
    try:
        message = {"photo": [{"file_id": "only"}]}
        out = tm._ingest_media(message)
        assert out == "[image could not be processed]", out
    finally:
        restore()


def test_poll_loop_does_not_ingest_for_unauthenticated_sender():
    """The auth gate must run before any media download: an ignored sender's
    photo must never reach _ingest_media (no file download for unauth users)."""
    import auth

    calls = {"ingest": 0}
    photo_update = {
        "update_id": 1,
        "message": {"chat": {"id": "5"}, "from": {"id": "9"},
                    "photo": [{"file_id": "f"}]},
    }

    saved = (tm._ingest_media, tm._api_call, auth.is_auth_enabled,
             auth.authenticate_channel_user, tm._running,
             tm._authenticated_user_id, tm._chat_id)

    def fake_api(method, params=None, **kw):
        if method == "getUpdates":
            tm._running = False          # stop the loop after one batch
            return [photo_update]
        return {}

    def fake_ingest(message):
        calls["ingest"] += 1
        return "[image]"

    tm._running = True
    tm._authenticated_user_id = None
    tm._chat_id = ""
    tm._api_call = fake_api
    tm._ingest_media = fake_ingest
    auth.is_auth_enabled = lambda: True
    auth.authenticate_channel_user = lambda *a, **k: "ignore"
    try:
        tm._poll_loop()
        assert calls["ingest"] == 0, "unauthenticated photo must not be ingested"
    finally:
        (tm._ingest_media, tm._api_call, auth.is_auth_enabled,
         auth.authenticate_channel_user, tm._running,
         tm._authenticated_user_id, tm._chat_id) = saved


if __name__ == "__main__":
    test_ingest_media_photo_returns_image_marker_and_sets_pending()
    test_ingest_media_pdf_returns_filename_and_text()
    test_ingest_media_non_pdf_document_returns_none()
    test_ingest_media_voice_returns_transcript()
    test_ingest_media_photo_download_failure_returns_marker()
    test_poll_loop_does_not_ingest_for_unauthenticated_sender()
    print("all telegram_media tests passed")
