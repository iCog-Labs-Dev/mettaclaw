"""Standalone launcher for the Telegram admin bot.

Run it for hands-on testing of the /history and /memory admin features without
booting the full MeTTa engine. In production the engine starts the bot itself
via ``channels.tg_channel.start_telegram``; this is only a convenience runner.

Required env vars:
  TG_BOT_TOKEN     your bot token from @BotFather
  OPENAI_API_KEY   any non-empty value works for /history & /memory testing
                   (only the message-moderation path actually calls OpenAI)
Optional:
  TG_CHAT_ID       a group chat id to bind to; not needed for DM admin commands

Make sure your numeric Telegram id is listed under admin_controls.admin_ids in
memory/telegram_profile.yaml, or the admin commands will silently ignore you.
"""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# config_helper builds an OpenAI client at import; a placeholder is fine here.
os.environ.setdefault("OPENAI_API_KEY", "sk-placeholder-local-test")

from channels import tg_channel as tg  # noqa: E402

token = os.environ.get("TG_BOT_TOKEN")
if not token:
    sys.exit("Set TG_BOT_TOKEN first, e.g.  export TG_BOT_TOKEN='123:abc'")

chat_id = os.environ.get("TG_CHAT_ID")  # None is fine for DM admin use

tg.start_telegram(token, chat_id)

print("Bot started. Open a DM with it and send /start, /history or /memory.")
print("admin_ids:", tg._channel.admin_ids)
print("history path:", tg._channel.history_path)
print("Press Ctrl+C to stop.")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    tg.stop_telegram()
    print("\nStopped.")
