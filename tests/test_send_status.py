from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_irc_send_reports_not_connected():
    from channels import irc

    assert irc.send_message("hello") == "SEND_ERROR|NOT_CONNECTED"


def test_mattermost_send_reports_not_connected():
    pytest.importorskip("requests")
    pytest.importorskip("websocket")
    from channels import mattermost

    assert mattermost.send_message("hello") == "SEND_ERROR|NOT_CONNECTED"


def test_tg_send_reports_not_connected():
    pytest.importorskip("aiogram")
    from channels.tg_channel import _TelegramChannel

    channel = _TelegramChannel()
    channel.connected = False
    channel.bot = None
    channel.loop = None
    channel.chat_id = None

    assert channel.send_message("hello") == "SEND_ERROR|NOT_CONNECTED"
