import asyncio
import os
import sys
import threading
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEDIA_DIR = os.path.dirname(_HERE)
_REPO_ROOT = os.path.dirname(os.path.dirname(_MEDIA_DIR))

# telegram_media.py pulls in pluginapi (src/) the same way core's
# channels/telegram.py does when loaded as a real plugin. It no longer needs
# channels/auth.py — the fork's channel uses its own admin_ids/allowed_chats
# authorization, not core's auth handshake.
sys.path.insert(0, _MEDIA_DIR)
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))
sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("OPENAI_API_KEY", "test-key")

import telegram_media as tm
import media_handler as mh
from aiogram.types import BufferedInputFile


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


def _fake_message(chat_id=1, chat_type="private", user_id=42, is_bot=False,
                   text=None, caption=None, photo=None, document=None,
                   voice=None, audio=None, video=None, reply_to_message=None,
                   message_id=1):
    answers = []

    async def answer(text, **kwargs):
        answers.append((text, kwargs))

    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, type=chat_type),
        from_user=SimpleNamespace(id=user_id, username="tester", full_name="Tester",
                                   is_bot=is_bot),
        text=text,
        caption=caption,
        photo=photo,
        document=document,
        voice=voice,
        audio=audio,
        video=video,
        reply_to_message=reply_to_message,
        message_id=message_id,
        answer=answer,
        _answers=answers,
    )


def _new_channel(admin_ids=(42,)):
    """A fresh _TelegramChannel loaded from the real plugin-local config, with
    admin_ids overridden so a private-chat admin test doesn't depend on the
    (empty by default) telegram_profile.yaml admin list."""
    ch = tm._TelegramChannel()
    ch.admin_ids = list(admin_ids)
    return ch


class FakeBot:
    """Stub aiogram Bot: download() fills the destination buffer; send_photo()
    records the call instead of hitting the network."""

    def __init__(self, download_bytes=b"raw-bytes"):
        self.download_bytes = download_bytes
        self.sent_photo = None

    async def download(self, file_obj, destination):
        destination.write(self.download_bytes)

    async def send_photo(self, chat_id, photo, caption=None, reply_to_message_id=None):
        self.sent_photo = {"chat_id": chat_id, "photo": photo, "caption": caption,
                            "reply_to_message_id": reply_to_message_id}
        return SimpleNamespace()


def test_photo_handler_buffers_image_and_queues_marker():
    ch = _new_channel()
    ch.bot = FakeBot()
    restore = _stub([
        (mh, "sanitize_image", lambda raw: b"sanitized-jpeg"),
        (mh, "image_to_data_uri", lambda img, mime: "data:image/jpeg;base64,AAAA"),
    ])
    calls = {}

    def fake_set_pending_media(media):
        calls["media"] = media
    mh.set_pending_media = fake_set_pending_media

    try:
        message = _fake_message(photo=[SimpleNamespace(), SimpleNamespace()])
        asyncio.run(ch._on_photo(message))
        assert len(ch._message_queue) == 1
        result = ch.get_last_message()
        assert result is not None and "[image]" in result, result
        assert calls["media"] == [{"type": "image_url",
                                    "image_url": {"url": "data:image/jpeg;base64,AAAA"}}]
    finally:
        restore()


def test_pdf_handler_extracts_text():
    ch = _new_channel()
    ch.bot = FakeBot()

    async def not_blocked(text):
        return False
    restore = _stub([
        (mh, "extract_pdf_text", lambda raw, filename: "extracted text here"),
        (tm, "is_category_blocked", not_blocked),
    ])
    try:
        message = _fake_message(document=SimpleNamespace(mime_type="application/pdf",
                                                           file_name="report.pdf"))
        asyncio.run(ch._on_document(message))
        assert len(ch._message_queue) == 1
        chat_id, display_text, reply_id, payload = ch._message_queue[0]
        assert "report.pdf" in display_text, display_text
        assert payload == {"media": None, "context": "extracted text here"}
    finally:
        restore()


def test_pdf_handler_rejects_non_pdf_document():
    ch = _new_channel()
    ch.bot = FakeBot()
    message = _fake_message(document=SimpleNamespace(mime_type="text/plain",
                                                       file_name="notes.txt"))
    asyncio.run(ch._on_document(message))
    assert len(ch._message_queue) == 0
    assert message._answers, "expected a rejection notice"
    assert "PDF" in message._answers[0][0]


def test_voice_handler_transcribes_audio():
    ch = _new_channel()
    ch.bot = FakeBot()

    async def not_blocked(text):
        return False
    restore = _stub([
        (mh, "transcribe_audio", lambda raw, filename: "hello from the transcript"),
        (tm, "is_category_blocked", not_blocked),
    ])
    try:
        message = _fake_message(voice=SimpleNamespace(file_id="v1", file_name=None))
        asyncio.run(ch._on_audio(message))
        assert len(ch._message_queue) == 1
        chat_id, display_text, reply_id, payload = ch._message_queue[0]
        assert "sent audio" in display_text, display_text
        assert payload == {"media": None, "context": "hello from the transcript"}
    finally:
        restore()


def test_muted_user_is_gated_from_message_queue():
    ch = _new_channel()

    async def not_blocked(text):
        return False
    restore = _stub([
        (tm, "get_spam_protection_config", lambda: {
            "time_window": 10, "message_limit": 1,
            "cooldown_duration": 120, "admin_alert_threshold": 3,
        }),
        (tm, "is_category_blocked", not_blocked),
    ])
    try:
        user = SimpleNamespace(id=99, username="spammer", full_name="Spammer", is_bot=False)
        # First call establishes history; second exceeds message_limit=1 and mutes.
        assert asyncio.run(ch.is_user_muted(user)) is False
        assert asyncio.run(ch.is_user_muted(user)) is True

        message = _fake_message(user_id=99, text="hello again")
        asyncio.run(ch._on_message(message))
        assert len(ch._message_queue) == 0, "muted user's message must not be queued"
    finally:
        restore()


def test_inbound_ethics_block_prevents_queueing():
    ch = _new_channel()

    async def blocked(text):
        return True
    restore = _stub([
        (tm, "is_category_blocked", blocked),
        (tm, "alert_ethics_violation", lambda tool_name, text=None: None),
    ])
    try:
        message = _fake_message(text="something unsafe")
        asyncio.run(ch._on_message(message))
        assert len(ch._message_queue) == 0, "blocked message must not be queued"
    finally:
        restore()


def test_send_photo_dispatches_expected_aiogram_call():
    ch = _new_channel()
    bot = FakeBot()
    ch.bot = bot
    ch.connected = True
    ch.chat_id = "555"
    ch._reply_to_id = None

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    ch.loop = loop
    try:
        ch.send_photo(b"image-bytes", caption="a cat")
        assert bot.sent_photo is not None
        assert bot.sent_photo["chat_id"] == "555"
        assert bot.sent_photo["caption"] == "a cat"
        assert isinstance(bot.sent_photo["photo"], BufferedInputFile)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2)


def test_admin_command_refuses_non_admin_allows_admin():
    """_purge_cmd (admin-only, private-DM-only) must refuse a non-admin and
    take no destructive effect, and must proceed for an admin."""
    ch = _new_channel(admin_ids=(42,))
    calls = {"deleted": False}

    class FakeChromaClient:
        def __init__(self, path=None):
            pass

        def delete_collection(self, name):
            calls["deleted"] = True

        def get_or_create_collection(self, name=None):
            return SimpleNamespace()

    fake_chromadb = SimpleNamespace(PersistentClient=FakeChromaClient)
    sys.modules["chromadb"] = fake_chromadb
    try:
        non_admin = _fake_message(chat_type="private", user_id=999)
        assert ch._is_admin_dm(non_admin) is False
        asyncio.run(ch._purge_cmd(non_admin))
        assert calls["deleted"] is False, "non-admin must not trigger the purge"
        assert non_admin._answers, "expected a refusal reply"
        assert "Admin commands only work in direct messages" in non_admin._answers[0][0]

        admin = _fake_message(chat_type="private", user_id=42)
        assert ch._is_admin_dm(admin) is True
        asyncio.run(ch._purge_cmd(admin))
        assert calls["deleted"] is True, "admin command must proceed"
    finally:
        del sys.modules["chromadb"]


def test_group_message_requires_tag_or_reply():
    """Untagged group chatter must be dropped; a tagged message or a reply to
    the bot must be queued."""
    ch = _new_channel()
    ch.bot_username = "mybot"
    ch.bot_id = 555

    async def not_blocked(text):
        return False
    restore = _stub([(tm, "is_category_blocked", not_blocked)])
    try:
        untagged = _fake_message(chat_type="group", user_id=1001, text="just chatting")
        asyncio.run(ch._on_message(untagged))
        assert len(ch._message_queue) == 0, "untagged group chatter must not be queued"

        tagged = _fake_message(chat_type="group", user_id=1002, text="@mybot hello there")
        asyncio.run(ch._on_message(tagged))
        assert len(ch._message_queue) == 1, "a message tagging the bot must be queued"

        reply_to_bot = _fake_message(
            chat_type="group", user_id=1003, text="answering you",
            reply_to_message=SimpleNamespace(from_user=SimpleNamespace(id=555)),
        )
        asyncio.run(ch._on_message(reply_to_bot))
        assert len(ch._message_queue) == 2, "a reply to the bot must be queued"
    finally:
        restore()


def test_dm_authorization_gates_non_admin_allows_admin():
    """_is_chat_authorized DM branch: with dm_enabled False (shipped default),
    a non-admin DM is not authorized; an admin DM is."""
    ch = _new_channel(admin_ids=(42,))
    ch.dm_enabled = False

    non_admin_dm = _fake_message(chat_type="private", user_id=7)
    assert ch._is_chat_authorized(non_admin_dm) is False

    admin_dm = _fake_message(chat_type="private", user_id=42)
    assert ch._is_chat_authorized(admin_dm) is True

    # End-to-end: an unauthorized DM must not reach the message queue.
    async def not_blocked(text):
        return False
    restore = _stub([(tm, "is_category_blocked", not_blocked)])
    try:
        blocked_msg = _fake_message(chat_type="private", user_id=7, text="hi")
        asyncio.run(ch._on_message(blocked_msg))
        assert len(ch._message_queue) == 0, "unauthorized DM must not be queued"
    finally:
        restore()


def test_plugin_registration_exposes_comm_channel():
    assert issubclass(tm.TelegramChannel, __import__("pluginapi").CommChannel)
    channel = tm.TelegramChannel()
    assert hasattr(channel, "config") and hasattr(channel, "receive") and hasattr(channel, "send")


if __name__ == "__main__":
    test_photo_handler_buffers_image_and_queues_marker()
    test_pdf_handler_extracts_text()
    test_pdf_handler_rejects_non_pdf_document()
    test_voice_handler_transcribes_audio()
    test_muted_user_is_gated_from_message_queue()
    test_inbound_ethics_block_prevents_queueing()
    test_send_photo_dispatches_expected_aiogram_call()
    test_admin_command_refuses_non_admin_allows_admin()
    test_group_message_requires_tag_or_reply()
    test_dm_authorization_gates_non_admin_allows_admin()
    test_plugin_registration_exposes_comm_channel()
    print("all telegram_media tests passed")
