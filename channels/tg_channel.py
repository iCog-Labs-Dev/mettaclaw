import asyncio
import time
import threading
import logging
import yaml
import os
import re

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BotCommand,
)
from src.config_helper import (
    is_category_blocked,
    get_spam_protection_config,
    get_history_admin_config,
    get_memory_admin_config,
)
from src import history_admin
from src import memory_admin


log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "telegram.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file)
    ]
)

class _TelegramChannel:
    """Telegram bot channel with windowed batching and bot-tag gating using aiogram."""

    def __init__(self, config_path=None):
        self.config_path = os.path.join(os.path.dirname(__file__),  "..", "memory", "telegram_profile.yaml")
        self.policy_path= os.path.join(os.path.dirname(__file__), "..", "memory", "policy.md")
        self.running = False
        self.thread = None
        self.loop = None
        self.bot = None
        self.dp = None
        self.connected = False
        self.chat_id = None
        self.allowed_chat_id = None
        self.allowed_chat_ids = set()
        
        self.bot_username = None
        self.bot_id = None
        self.msg_lock = threading.Lock()
        
        # Default settings
        self.reply_only_on_tag = True
        self.reply_on_reply = True
        self.admin_ids = []
        self.dm_enabled = False
        self.restrict_to_config_chat = True
        self.allow_group_bots = False
        self.reply_constraints = None

        # Conversation-history admin (memory/history.metta)
        self.history_path = history_admin.DEFAULT_HISTORY_PATH
        self.history_cfg = {"enabled": True, "inspect": True, "delete": True, "purge": True}

        # Long-term (ChromaDB) memory admin
        self._repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.memory_cfg = {
            "enabled": True, "inspect": True, "delete": True, "purge": True,
            "db_path": "./chroma_db", "collection_name": "memories",
        }
        
        # Policy messages
        self.start_msg = "Telegram mode active."
        self.about_msg = "I am a MeTTaClaw agent."
        self.privacy_msg = "No sensitive data is stored."
        
        # Load config and policies if they exist
        self.load_config(self.config_path)
        self.load_policies()

        self._muted_users = {}
        self._user_msg_rates = {}
        self._user_mute_counts = {}
        
        # Windowed batching state
        self._message_queue = []
        self._reply_to_ids = {}
        self._paused_chats = set()
        self.search_disabled = False
        self._ready_windows = []
        self._polling_task = None
        self._typing_threads = {}

    def _normalize_chat_id(self, chat_id):
        if chat_id is None:
            return None

        chat_id = str(chat_id).strip("\"' ")
        if not chat_id:
            return None

        if not chat_id.startswith("-") and chat_id.isdigit() and len(chat_id) > 10:
            chat_id = f"-{chat_id}"

        return chat_id

    def _normalize_chat_ids(self, chat_ids):
        if chat_ids is None:
            return set()

        if isinstance(chat_ids, (list, tuple, set)):
            values = chat_ids
        else:
            values = str(chat_ids).split(",")

        normalized = set()
        for chat_id in values:
            value = self._normalize_chat_id(chat_id)
            if value:
                normalized.add(value)
        return normalized

    def _is_allowed_chat(self, chat_id):
        if not self.restrict_to_config_chat:
            return True

        if not self.allowed_chat_ids:
            return True

        return self._normalize_chat_id(chat_id) in self.allowed_chat_ids

    def load_config(self, config_path):
        """Load bot configuration from a YAML file."""
        if not os.path.exists(config_path):
            print(f"Config file {config_path} not found. Using defaults.")
            logging.warning(f"Config file {config_path} not found. Using defaults.")
            return

        try:
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)
            
            tg_cfg = config.get("telegram", {})
            self.window_seconds = tg_cfg.get("batching", {}).get("window_seconds", 10)
            self.reply_only_on_tag = tg_cfg.get("reply_only_when_directly_tagged", True)
            self.reply_on_reply = tg_cfg.get("reply_on_reply_to_bot", True)
            self.dm_enabled = tg_cfg.get("dm_support", {}).get("enabled", False)
            self.restrict_to_config_chat = tg_cfg.get("restrict_to_config_chat", True)
            self.allow_group_bots = tg_cfg.get("allow_group_bots", False)
            self.allowed_chat_ids = self._normalize_chat_ids(tg_cfg.get("allowed_chats", []))
            self.allowed_chat_id = next(iter(self.allowed_chat_ids), None)
            self.admin_ids = config.get("admin_controls", {}).get("admin_ids", [])
            self.reply_constraints = tg_cfg.get("reply_constraints", {})
            self.history_cfg = get_history_admin_config()
            self.memory_cfg = get_memory_admin_config()

            logging.info(f"Loaded config from {config_path}: window={self.window_seconds}s, tag_only={self.reply_only_on_tag}")
        except Exception as e:
            logging.error(f"Error loading config {config_path}: {e}")

    def load_policies(self):
        """Load and parse policy sections from a markdown file."""
        
        if not os.path.exists(self.policy_path):
            logging.warning(f"Policy file {self.policy_path} not found. Using defaults.")
            return

        try:
            with open(self.policy_path, "r") as f:
                content = f.read()
            
            sections = {}
            current_section = None
            current_text = []
            
            for line in content.split("\n"):
                if line.startswith("# "):
                    if current_section:
                        sections[current_section] = "\n".join(current_text).strip()
                    current_section = line[2:].strip().upper()
                    current_text = []
                elif current_section:
                    current_text.append(line)
            
            if current_section:
                sections[current_section] = "\n".join(current_text).strip()
            
            self.start_msg = sections.get("START", self.start_msg)
            self.about_msg = sections.get("ABOUT", self.about_msg)
            self.privacy_msg = sections.get("PRIVACY", self.privacy_msg)
            
            logging.info(f"Loaded policies from {self.policy_path}: sections={list(sections.keys())}")
        except Exception as e:
            logging.error(f"Error loading policies {self.policy_path}: {e}")

    def get_last_message(self):
        """Retrieve and consume the most recent processed window, thread-safe."""
        with self.msg_lock:
            if self._message_queue:
                ready_chat_id, text, reply_id = self._message_queue.pop(0)

                if not self._is_allowed_chat(ready_chat_id) and ready_chat_id not in self.admin_ids:
                        return None
                
                self.chat_id = ready_chat_id
                self._reply_to_id = reply_id
                self._start_typing(str(ready_chat_id))
                return f"[{ready_chat_id}] [{reply_id}] {text}"
            return None
    
    def _is_admin_dm(self, message: types.Message) -> bool:

        return (
            message.chat.type == "private"
            and message.from_user is not None
            and message.from_user.id in self.admin_ids
        )
    
    def _is_chat_authorized(self, message: types.Message, user_id_override: int = None) -> bool:
        """Check if the chat and user are authorized to interact with the bot."""
        
        # Handle Dms
        if message.chat.type == "private":
            user_id = user_id_override if user_id_override is not None else getattr(message.from_user, "id", None)
            if user_id not in self.admin_ids and not self.dm_enabled:
                return False
            return True
        
        # Handle Groups
        if not self._is_allowed_chat(message.chat.id):
            return False
                
        return True
    
    async def _start_cmd(self, message: types.Message):
        """Handle the /start command with interactive buttons."""
        if not self._is_chat_authorized(message):
            return
        
        if not self._is_admin_dm(message):
            return await message.answer("❌ Admin commands only work in direct messages.")

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="ℹ️ About", callback_data="show_about")
        builder.button(text="🛡️ Privacy", callback_data="show_privacy")

        if message.from_user and message.from_user.id in self.admin_ids:
            builder.button(text="⚙️ Admin Panel", callback_data="admin_panel")

        await message.answer(self.start_msg, reply_markup=builder.as_markup())

        # Give admins a persistent bottom button so they never type /history.
        if message.from_user and message.from_user.id in self.admin_ids:
            await message.answer(
                "Admin quick actions ⬇️ — tap 📜 History anytime.",
                reply_markup=self._admin_reply_kb(),
            )

    async def _about_cmd(self, message: types.Message):
        """Handle /about command."""

        await message.answer(self.about_msg)

    async def _privacy_cmd(self, message: types.Message):
        """Handle /privacy command."""
        if not self._is_chat_authorized(message):
            return

        await message.answer(self.privacy_msg)

    async def _kill_cmd(self, message: types.Message):
        """Handle global kill switch (admin only)."""
        if not self._is_admin_dm(message):
            return await message.answer("❌ Admin commands only work in direct messages.")

        await message.answer("⚠️ Global Kill Switch activated. Shutting down...")
        logging.critical(f"KILLED by admin {message.from_user.id}")
        self.stop()
        os._exit(0)
    
    async def _pause_cmd(self, message: types.Message):
        """Handle /pause command (admin only)."""
        if not self._is_chat_authorized(message):
            return
        
        if not self._is_admin_dm(message):
             return await message.answer("❌ Admin commands only work in direct messages.")
        
        target_chat = self.allowed_chat_id or getattr(message.chat, "id", None)
        args = message.text.split()
        if len(args) > 1:
            target_chat = args[1]
            
        if target_chat in self._paused_chats:
            self._paused_chats.remove(target_chat)
            await message.answer(f"▶️ Chat {target_chat} unpaused.")
        else:
            self._paused_chats.add(target_chat)
            await message.answer(f"⏸️ Chat {target_chat} paused.")

    async def _togglesearch_cmd(self, message: types.Message):
        """Handle /togglesearch command (admin only)."""
        if not self._is_admin_dm(message):
            return await message.answer("❌ Admin commands only work in direct messages.")

        self.search_disabled = not self.search_disabled
        state = "DISABLED" if self.search_disabled else "ENABLED"
        await message.answer(f"🔍 Web search is now {state}.")
    
    async def _purge_cmd(self, message: types.Message):
        """Handle /purge command (admin only)."""
        if not self._is_admin_dm(message):
            return await message.answer("❌ Admin commands only work in direct messages.")

        try:
            import chromadb
            client = chromadb.PersistentClient(path="./chroma_db")
            client.delete_collection("memories")
            client.get_or_create_collection(name="memories")
            await message.answer("🗑️ Long-term memory purged successfully.")
        except Exception as e:
            await message.answer(f"❌ Failed to purge memory: {e}")


    # ── Conversation-history admin (memory/history.metta) ────────────
    # The heavy lifting (parsing, stats, delete/purge, view-models and
    # button specs) lives in src/history_admin.py. The methods below are
    # thin transport: gate -> call -> render.

    def _rows_to_markup(self, rows):
        """Convert history_admin button rows -> aiogram InlineKeyboardMarkup."""
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
                for row in rows
            ]
        )

    def _admin_reply_kb(self):
        """Persistent bottom keyboard giving admins one-tap History/Memory buttons."""
        return ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📜 History"), KeyboardButton(text="🧠 Memory")]],
            resize_keyboard=True,
            is_persistent=True,
        )

    async def _history_guard(self, message: types.Message, need: str = None) -> bool:
        """Admin-DM + config gate shared by every history command."""
        if not self._is_admin_dm(message):
            await message.answer("❌ Admin commands only work in direct messages.")
            return False
        if not self.history_cfg.get("enabled", True):
            await message.answer("⚠️ History admin is disabled by config.")
            return False
        if need and not self.history_cfg.get(need, True):
            await message.answer(f"⚠️ History {need} is disabled by config.")
            return False
        return True

    async def _history_cmd(self, message: types.Message):
        """Open the history admin menu (buttons)."""
        if not await self._history_guard(message):
            return
        await message.answer(
            "🧠 History admin — choose an action:",
            reply_markup=self._rows_to_markup(history_admin.menu_buttons()),
        )

    async def _history_stats_cmd(self, message: types.Message):
        if not await self._history_guard(message, "inspect"):
            return
        await message.answer(
            history_admin.format_stats(self.history_path),
            reply_markup=self._rows_to_markup(history_admin.menu_buttons()),
        )

    async def _history_list_cmd(self, message: types.Message):
        if not await self._history_guard(message, "inspect"):
            return
        parts = (message.text or "").split()
        page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        text, page_entries = history_admin.format_list_page(self.history_path, page)
        total = len(history_admin.read_entries(self.history_path))
        await message.answer(
            text,
            reply_markup=self._rows_to_markup(
                history_admin.list_page_buttons(page_entries, page, total)
            ),
        )

    async def _history_get_cmd(self, message: types.Message):
        if not await self._history_guard(message, "inspect"):
            return
        parts = (message.text or "").split()
        if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
            return await message.answer("Usage: /history_get <index>")
        entry = history_admin.get_entry(self.history_path, int(parts[1]))
        if not entry:
            return await message.answer("ℹ️ Entry not found.")
        await message.answer(
            history_admin.format_entry(entry),
            reply_markup=self._rows_to_markup(history_admin.entry_buttons(entry.index)),
        )

    async def _history_delete_cmd(self, message: types.Message):
        if not await self._history_guard(message, "delete"):
            return
        parts = (message.text or "").split()
        if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
            return await message.answer("Usage: /history_delete <index>")
        idx = int(parts[1])
        entry = history_admin.get_entry(self.history_path, idx)
        if not entry:
            return await message.answer("ℹ️ Entry not found.")
        fp = history_admin.fingerprint(entry.raw)
        await message.answer(
            f"⚠️ Delete history entry #{idx} ({entry.timestamp})? This cannot be undone.",
            reply_markup=self._rows_to_markup(history_admin.delete_confirm_buttons(idx, fp)),
        )

    async def _history_purge_cmd(self, message: types.Message):
        if not await self._history_guard(message, "purge"):
            return
        stats = history_admin.history_stats(self.history_path)
        await message.answer(
            f"⚠️ Purge ALL {stats['entries']} history entries? This cannot be undone.",
            reply_markup=self._rows_to_markup(history_admin.purge_confirm_buttons()),
        )

    async def _edit(self, callback: types.CallbackQuery, text: str, rows):
        """Edit the message in place; fall back to a new message if needed."""
        markup = self._rows_to_markup(rows)
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except Exception:
            await callback.message.answer(text, reply_markup=markup)

    async def _handle_history_callback(self, callback: types.CallbackQuery):
        """Route 'hist:*' button presses to history_admin operations."""
        if not (
            callback.message
            and callback.message.chat.type == "private"
            and callback.from_user.id in self.admin_ids
        ):
            return await callback.answer("❌ Admins only (use a direct message).", show_alert=True)

        parsed = history_admin.parse_callback(callback.data)
        if not parsed:
            return await callback.answer()
        action, arg = parsed
        cfg, path = self.history_cfg, self.history_path

        def _gate(flag):
            return cfg.get("enabled", True) and cfg.get(flag, True)

        try:
            if action == "menu":
                await self._edit(callback, "🧠 History admin — choose an action:", history_admin.menu_buttons())
            elif action == "stats":
                if not _gate("inspect"):
                    return await callback.answer("Inspect disabled.", show_alert=True)
                await self._edit(callback, history_admin.format_stats(path), history_admin.menu_buttons())
            elif action == "list":
                if not _gate("inspect"):
                    return await callback.answer("Inspect disabled.", show_alert=True)
                page = int(arg) if arg and arg.isdigit() else 1
                text, page_entries = history_admin.format_list_page(path, page)
                total = len(history_admin.read_entries(path))
                await self._edit(callback, text, history_admin.list_page_buttons(page_entries, page, total))
            elif action == "view":
                if not _gate("inspect"):
                    return await callback.answer("Inspect disabled.", show_alert=True)
                entry = history_admin.get_entry(path, int(arg))
                if not entry:
                    return await callback.answer("Entry not found.", show_alert=True)
                await self._edit(callback, history_admin.format_entry(entry), history_admin.entry_buttons(entry.index))
            elif action == "delask":
                if not _gate("delete"):
                    return await callback.answer("Delete disabled.", show_alert=True)
                idx = int(arg)
                entry = history_admin.get_entry(path, idx)
                if not entry:
                    return await callback.answer("Entry not found.", show_alert=True)
                fp = history_admin.fingerprint(entry.raw)
                await self._edit(
                    callback,
                    f"⚠️ Delete history entry #{idx} ({entry.timestamp})? This cannot be undone.",
                    history_admin.delete_confirm_buttons(idx, fp),
                )
            elif action == "del":
                if not _gate("delete"):
                    return await callback.answer("Delete disabled.", show_alert=True)
                idx_str, _, fp = (arg or "").partition(":")
                removed = history_admin.delete_entry(path, int(idx_str), fp or None)
                msg = (
                    f"✅ Deleted entry ({removed.timestamp})." if removed
                    else "ℹ️ Entry already changed or removed — nothing deleted."
                )
                await self._edit(callback, msg, history_admin.menu_buttons())
            elif action == "purgeask":
                if not _gate("purge"):
                    return await callback.answer("Purge disabled.", show_alert=True)
                stats = history_admin.history_stats(path)
                await self._edit(callback, f"⚠️ Purge ALL {stats['entries']} history entries? This cannot be undone.", history_admin.purge_confirm_buttons())
            elif action == "purge":
                if not _gate("purge"):
                    return await callback.answer("Purge disabled.", show_alert=True)
                n = history_admin.purge(path)
                await self._edit(callback, f"🗑️ Purged {n} history entries.", history_admin.menu_buttons())
            await callback.answer()
        except Exception as e:
            logging.error(f"History callback error ({callback.data}): {e}")
            await callback.answer(f"Error: {e}", show_alert=True)

    # ── Long-term (ChromaDB) memory admin ────────────────────────────
    # Logic lives in src/memory_admin.py; these methods are thin transport.

    def _open_memory(self):
        """Open the configured Chroma collection (raises on failure)."""
        db_path = memory_admin.resolve_db_path(self.memory_cfg.get("db_path"), self._repo_root)
        return memory_admin.open_collection(db_path, self.memory_cfg.get("collection_name", "memories"))

    async def _memory_guard(self, message: types.Message, need: str = None) -> bool:
        if not self._is_admin_dm(message):
            await message.answer("❌ Admin commands only work in direct messages.")
            return False
        if not self.memory_cfg.get("enabled", True):
            await message.answer("⚠️ Memory admin is disabled by config.")
            return False
        if need and not self.memory_cfg.get(need, True):
            await message.answer(f"⚠️ Memory {need} is disabled by config.")
            return False
        return True

    async def _memory_cmd(self, message: types.Message):
        if not await self._memory_guard(message):
            return
        await message.answer(
            "🧠 Long-term memory admin — choose an action:",
            reply_markup=self._rows_to_markup(memory_admin.menu_buttons()),
        )

    async def _memory_stats_cmd(self, message: types.Message):
        if not await self._memory_guard(message, "inspect"):
            return
        try:
            client, col = self._open_memory()
            db_path = memory_admin.resolve_db_path(self.memory_cfg.get("db_path"), self._repo_root)
            await message.answer(
                memory_admin.format_stats(client, col, db_path),
                reply_markup=self._rows_to_markup(memory_admin.menu_buttons()),
            )
        except Exception as e:
            await message.answer(f"❌ Memory store unavailable: {e}")

    async def _memory_list_cmd(self, message: types.Message):
        if not await self._memory_guard(message, "inspect"):
            return
        parts = (message.text or "").split()
        page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        try:
            _, col = self._open_memory()
            recs, total = memory_admin.recent_records(col)
            page = max(1, min(page, memory_admin.page_count(total)))
            page_recs = memory_admin.format_list_page(recs, total, page)
            start = (page - 1) * memory_admin.DEFAULT_PAGE_SIZE
            await message.answer(
                memory_admin.list_page_text(page_recs, start, total, page),
                reply_markup=self._rows_to_markup(
                    memory_admin.list_page_buttons(page_recs, start, page, total)
                ),
            )
        except Exception as e:
            await message.answer(f"❌ Memory store unavailable: {e}")

    async def _memory_get_cmd(self, message: types.Message):
        if not await self._memory_guard(message, "inspect"):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            return await message.answer("Usage: /memory_get <id>")
        try:
            _, col = self._open_memory()
            rec = memory_admin.get_record(col, parts[1].strip())
            if not rec:
                return await message.answer("ℹ️ Memory id not found.")
            await message.answer(memory_admin._truncate(
                f"🧠 Memory Record\nID: {rec['id']}\nTimestamp: {rec['timestamp']}\n\n{rec['doc']}"))
        except Exception as e:
            await message.answer(f"❌ Memory store unavailable: {e}")

    async def _memory_delete_cmd(self, message: types.Message):
        if not await self._memory_guard(message, "delete"):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            return await message.answer("Usage: /memory_delete <id>")
        try:
            _, col = self._open_memory()
            ok = memory_admin.delete_record(col, parts[1].strip())
            await message.answer(
                f"✅ Deleted memory record: {parts[1].strip()}" if ok else "ℹ️ Memory id not found."
            )
        except Exception as e:
            await message.answer(f"❌ Memory store unavailable: {e}")

    async def _handle_memory_callback(self, callback: types.CallbackQuery):
        """Route 'mem:*' button presses to memory_admin operations."""
        if not (
            callback.message
            and callback.message.chat.type == "private"
            and callback.from_user.id in self.admin_ids
        ):
            return await callback.answer("❌ Admins only (use a direct message).", show_alert=True)

        parsed = memory_admin.parse_callback(callback.data)
        if not parsed:
            return await callback.answer()
        action, arg = parsed
        cfg = self.memory_cfg

        def _gate(flag):
            return cfg.get("enabled", True) and cfg.get(flag, True)

        try:
            if action == "menu":
                await self._edit(callback, "🧠 Long-term memory admin — choose an action:", memory_admin.menu_buttons())
                return await callback.answer()

            client, col = self._open_memory()
            db_path = memory_admin.resolve_db_path(cfg.get("db_path"), self._repo_root)

            if action == "stats":
                if not _gate("inspect"):
                    return await callback.answer("Inspect disabled.", show_alert=True)
                await self._edit(callback, memory_admin.format_stats(client, col, db_path), memory_admin.menu_buttons())
            elif action == "list":
                if not _gate("inspect"):
                    return await callback.answer("Inspect disabled.", show_alert=True)
                page = int(arg) if arg and arg.isdigit() else 1
                recs, total = memory_admin.recent_records(col)
                page = max(1, min(page, memory_admin.page_count(total)))
                page_recs = memory_admin.format_list_page(recs, total, page)
                start = (page - 1) * memory_admin.DEFAULT_PAGE_SIZE
                await self._edit(
                    callback,
                    memory_admin.list_page_text(page_recs, start, total, page),
                    memory_admin.list_page_buttons(page_recs, start, page, total),
                )
            elif action == "view":
                if not _gate("inspect"):
                    return await callback.answer("Inspect disabled.", show_alert=True)
                pos = int(arg)
                recs, _ = memory_admin.recent_records(col)
                rec = memory_admin.resolve_record(recs, pos)
                if not rec:
                    return await callback.answer("Record not found.", show_alert=True)
                await self._edit(callback, memory_admin.format_record(rec, pos), memory_admin.record_buttons(pos))
            elif action == "delask":
                if not _gate("delete"):
                    return await callback.answer("Delete disabled.", show_alert=True)
                pos = int(arg)
                recs, _ = memory_admin.recent_records(col)
                rec = memory_admin.resolve_record(recs, pos)
                if not rec:
                    return await callback.answer("Record not found.", show_alert=True)
                fp = memory_admin.fingerprint(rec["doc"])
                await self._edit(callback, f"⚠️ Delete memory #{pos + 1}? This cannot be undone.", memory_admin.delete_confirm_buttons(pos, fp))
            elif action == "del":
                if not _gate("delete"):
                    return await callback.answer("Delete disabled.", show_alert=True)
                pos_str, _, fp = (arg or "").partition(":")
                recs, _ = memory_admin.recent_records(col)
                rec = memory_admin.resolve_record(recs, int(pos_str), fp or None)
                if rec and memory_admin.delete_record(col, rec["id"]):
                    msg = f"✅ Deleted memory ({rec['timestamp']})."
                else:
                    msg = "ℹ️ Record already changed or removed — nothing deleted."
                await self._edit(callback, msg, memory_admin.menu_buttons())
            elif action == "purgeask":
                if not _gate("purge"):
                    return await callback.answer("Purge disabled.", show_alert=True)
                await self._edit(callback, f"⚠️ Purge ALL {col.count()} memory records? This cannot be undone.", memory_admin.purge_confirm_buttons())
            elif action == "purge":
                if not _gate("purge"):
                    return await callback.answer("Purge disabled.", show_alert=True)
                memory_admin.purge(client, col)
                await self._edit(callback, "🗑️ Long-term memory purged.", memory_admin.menu_buttons())
            await callback.answer()
        except Exception as e:
            logging.error(f"Memory callback error ({callback.data}): {e}")
            await callback.answer(f"Error: {e}", show_alert=True)

    async def _on_callback_query(self, callback: types.CallbackQuery):
        """Handle button clicks."""
        if callback.data and callback.data.startswith(history_admin.CB_PREFIX + ":"):
            return await self._handle_history_callback(callback)
        if callback.data and callback.data.startswith(memory_admin.CB_PREFIX + ":"):
            return await self._handle_memory_callback(callback)

        if not self._is_chat_authorized(callback.message, user_id_override=callback.from_user.id):
            await callback.answer("❌ This chat is not authorized.", show_alert=True)
            return
        
        if callback.data == "show_about":
            await callback.message.answer(self.about_msg)
        elif callback.data == "show_privacy":
            await callback.message.answer(self.privacy_msg)
        elif callback.data == "admin_panel":
            if callback.from_user.id in self.admin_ids:
                cmd_list = (
                    "🛠 **Admin Commands:**\n"
                    "/pause [chat_id] - Pause/unpause a chat\n"
                    "/togglesearch - Enable/Disable Web Search\n"
                    "/purge - Wipe ChromaDB Memory\n"
                    "/history - Manage conversation history (buttons)\n"
                    "/memory - Manage long-term memory (buttons)\n"
                    "/kill - Shutdown Bot globally"
                )
                await callback.message.answer(
                    cmd_list,
                    reply_markup=self._rows_to_markup([[
                        ("📜 Manage History", history_admin.cb("menu")),
                        ("🧠 Manage Memory", memory_admin.cb("menu")),
                    ]]),
                )
            else:
                await callback.message.answer("❌ Access denied.")
        await callback.answer()
    
    async def _send_block_notice(self, message: types.Message, text: str):
        try:
            await message.answer(text, reply_to_message_id=message.message_id)
        except Exception as e:
            logging.error(f"Failed to send block notice: {e}")

    async def _on_message(self, message: types.Message):
        """Capture group messages into the buffer; flag reply if bot is tagged."""
        if message.text is None:
            return
        
        if message.chat.id in self._paused_chats:
            return

        if not self._is_chat_authorized(message):
            return
        
        # Filter out messages from other bots and muted users
        if message.from_user:
            if message.chat.type in ["group", "supergroup"]:
                if message.from_user.is_bot and not self.allow_group_bots:
                    return
            
            if await self.is_user_muted(message.from_user):
                return
        
        has_media = bool(message.photo or message.video or message.audio or message.voice)
        if has_media and not self.reply_constraints.get("allow_media", False):
            await self._send_block_notice(message, "Media messages are not supported here. Please send text instead.")
            return

        has_files = bool(message.document)
        if has_files and not self.reply_constraints.get("allow_files", False):
            await self._send_block_notice(message, "File uploads are not supported here. Please send text instead.")
            return
        
        if message.chat is not None:
            chat_id = message.chat.id
            
        user = message.from_user
        name = "unknown user" if user is None else (user.username or user.full_name or str(user.id))
        name = f"@{name}" if name == user.username else name
        text = message.text

        if await is_category_blocked(text):
            logging.warning(f"Ethics/Security pass rejected incoming message from {name}: {text}")
            message = "From: " + user.username + ": " + text if user and user.username else text
            alert_ethics_violation("incoming_message", message)
            return

        is_private = message.chat.type == "private"
        if not is_private:
            is_tagged = self.bot_username and f"@{self.bot_username}" in text
            is_reply = (
                self.reply_on_reply and
                message.reply_to_message and
                message.reply_to_message.from_user and
                message.reply_to_message.from_user.id == self.bot_id
            )

            if self.reply_only_on_tag and not (is_tagged or is_reply):
                return
        
        with self.msg_lock:
            self._message_queue.append((chat_id, f"{name}: {text}", message.message_id))
            

    async def is_user_muted(self, user: types.User):
        """Feature: User mute / cool-down after repeated abuse."""
        spam_config = get_spam_protection_config()
        time_window = spam_config["time_window"]
        message_limit = spam_config["message_limit"]
        cooldown_duration = spam_config["cooldown_duration"]
        admin_alert_threshold = spam_config["admin_alert_threshold"]
        user_id = user.id

        if user_id in self._muted_users:
            if time.time() < self._muted_users[user_id]:
                return True
            else:
                del self._muted_users[user_id]
                
        now = time.time()
        history = self._user_msg_rates.get(user_id, [])
        history = [ts for ts in history if now - ts < time_window]
        history.append(now)
        self._user_msg_rates[user_id] = history
        
        if len(history) > message_limit:
            mute_count = self._user_mute_counts.get(user_id, 0) + 1
            self._user_mute_counts[user_id] = mute_count

            username = user.username or user.full_name or str(user_id)
            logging.warning(f"User with id: {user_id} | username: {username} muted for spamming.")
            self._muted_users[user_id] = now + cooldown_duration
            
            if mute_count >= admin_alert_threshold:
                for admin_id in self.admin_ids:
                    try:
                        alert_msg = (f"🚨 **Spam Alert** 🚨\n"
                                        f"User @{username} (ID: {user_id}) has been temporarily muted for spamming.\n"
                                        f"Total times muted: {mute_count}")
                        await self.bot.send_message(chat_id=admin_id, text=alert_msg)
                    except Exception as e:
                        logging.error(f"Failed to notify admin {admin_id}: {e}")
                        
            return True
            
        return False

    async def _on_media_rejected(self, message: types.Message):
        if not self._is_chat_authorized(message):
            return
        
        caption = message.caption or ""

        is_tagged = (
            self.bot_username and
            f"@{self.bot_username}".lower() in caption.lower()
        )

        is_reply = (
            self.reply_on_reply and
            message.reply_to_message and
            message.reply_to_message.from_user and
            message.reply_to_message.from_user.id == self.bot_id
        )

        if is_tagged or is_reply or message.chat.type == "private":
            logging.info("Denied capability invoked: Media/File uploaded.")
            await self._send_block_notice(
                message,
                "I can only process text messages here. Please resend your request as text."
            )

    async def _runner(self, token):
        """Build the aiogram bot, start polling, and run until stopped."""
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        
        try:
            # Get bot info for tag detection
            bot_info = await self.bot.get_me()
            self.bot_username = bot_info.username
            self.bot_id = bot_info.id

            # Register the ☰ command menu so admins tap instead of typing.
            try:
                await self.bot.set_my_commands([
                    BotCommand(command="history", description="📜 Manage conversation history"),
                    BotCommand(command="history_stats", description="📊 History stats"),
                    BotCommand(command="history_list", description="📜 List history entries"),
                    BotCommand(command="history_get", description="🔍 View one entry: /history_get <n>"),
                    BotCommand(command="history_purge", description="🗑 Purge all history"),
                    BotCommand(command="memory", description="🧠 Manage long-term memory"),
                    BotCommand(command="memory_stats", description="📊 Memory stats"),
                    BotCommand(command="memory_list", description="🧠 List memory records"),
                    BotCommand(command="memory_get", description="🔍 View one record: /memory_get <id>"),
                ])
            except Exception as e:
                logging.error(f"set_my_commands failed: {e}")

            chat_ids_for_admin_scan = list(self.allowed_chat_ids)
            if self.chat_id:
                normalized_chat_id = self._normalize_chat_id(self.chat_id)
                if normalized_chat_id:
                    chat_ids_for_admin_scan.append(normalized_chat_id)

            for eval_chat_id in dict.fromkeys(chat_ids_for_admin_scan):
                try:
                    admins = await self.bot.get_chat_administrators(eval_chat_id)
                    for admin in admins:
                        if admin.user.id not in self.admin_ids:
                            self.admin_ids.append(int(admin.user.id))
                    logging.info(f"Loaded admins from group {eval_chat_id}. Total admins: {len(self.admin_ids)}")
                except Exception as e:
                    logging.error(f"Failed to fetch administrators for chat {eval_chat_id}: {e}")
            
            self.dp.message.register(self._start_cmd, Command("start"))
            self.dp.message.register(self._about_cmd, Command("about"))
            self.dp.message.register(self._privacy_cmd, Command("privacy"))
            self.dp.message.register(self._kill_cmd, Command("kill"))
            self.dp.message.register(self._pause_cmd, Command("pause"))
            self.dp.message.register(self._togglesearch_cmd, Command("togglesearch"))
            self.dp.message.register(self._purge_cmd, Command("purge"))
            self.dp.message.register(self._history_cmd, Command("history"))
            self.dp.message.register(self._history_stats_cmd, Command("history_stats"))
            self.dp.message.register(self._history_list_cmd, Command("history_list"))
            self.dp.message.register(self._history_get_cmd, Command("history_get"))
            self.dp.message.register(self._history_delete_cmd, Command("history_delete"))
            self.dp.message.register(self._history_purge_cmd, Command("history_purge"))
            self.dp.message.register(self._memory_cmd, Command("memory"))
            self.dp.message.register(self._memory_stats_cmd, Command("memory_stats"))
            self.dp.message.register(self._memory_list_cmd, Command("memory_list"))
            self.dp.message.register(self._memory_get_cmd, Command("memory_get"))
            self.dp.message.register(self._memory_delete_cmd, Command("memory_delete"))
            # Persistent reply-keyboard buttons -> open the menus (no typing).
            self.dp.message.register(self._history_cmd, F.text == "📜 History")
            self.dp.message.register(self._memory_cmd, F.text == "🧠 Memory")
            self.dp.callback_query.register(self._on_callback_query)
            self.dp.message.register(self._on_message, F.text)
            self.dp.message.register(self._on_media_rejected, ~F.text)

            self.connected = True
            self._polling_task = asyncio.create_task(self.dp.start_polling(self.bot, skip_updates=True, handle_signals=False))
            await self._polling_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logging.error(f"Telegram runner error: {e}")
        finally:
            self.connected = False
            await self.bot.session.close()

    def _thread_main(self, token):
        """Create a dedicated asyncio event loop and run the bot in it."""
        loop = asyncio.new_event_loop()
        self.loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._runner(token))
        except Exception as e:
            logging.error(f"Telegram runner error in thread: {e}")
        finally:
            loop.close()
        self.loop = None

    def start(self, token, chat_id=None, config_path=None):
        """Launch the Telegram bot on a daemon thread and begin polling."""
        self.running = True
        # Reload config if path provided
        if config_path is None:
            self.load_config(self.config_path)

        runtime_chat_ids = self._normalize_chat_ids(chat_id)
        if runtime_chat_ids:
            self.allowed_chat_ids.update(runtime_chat_ids)
            self.allowed_chat_id = next(iter(self.allowed_chat_ids), None)
            self.chat_id = next(iter(runtime_chat_ids))
        else:
            self.chat_id = self.allowed_chat_id
            
        self.thread = threading.Thread(target=self._thread_main, args=(token,), daemon=True)
        self.thread.start()
        return self.thread

    def stop(self):
        """Signal the polling loop to stop gracefully."""
        self.running = False
        if self.loop and self._polling_task:
            self.loop.call_soon_threadsafe(self._polling_task.cancel)

    def _start_typing(self, chat_id):
        """Start a background thread that sends typing action every 4s until stopped."""
        self._stop_typing(chat_id)
        stop_event = threading.Event()
        self._typing_threads[chat_id] = stop_event

        def typing_loop():
            while not stop_event.is_set():
                if self.connected and self.bot and self.loop:
                    asyncio.run_coroutine_threadsafe(
                        self.bot.send_chat_action(chat_id=chat_id, action="typing"),
                        self.loop
                    )
                stop_event.wait(4)

        t = threading.Thread(target=typing_loop, daemon=True)
        t.start()

    def _stop_typing(self, chat_id):
        """Stop the typing indicator for a given chat."""
        stop_event = self._typing_threads.pop(chat_id, None)
        if stop_event:
            stop_event.set()

    def send_message(self, text, chat_id=None, reply_to_id=None):
        """Send a text message to the active chat, dispatched to the bot's event loop."""
        text = text.replace("\\n", "\n")

        target_chat_id = chat_id or self.chat_id
        self._stop_typing(str(target_chat_id))
        target_reply_id = reply_to_id or (self._reply_to_id if target_chat_id == self.chat_id else None)
        
        if not self.connected or self.bot is None or self.loop is None or target_chat_id is None:
            return
        
        fut = asyncio.run_coroutine_threadsafe(
            self.bot.send_message(chat_id=target_chat_id,
                                  text=text,
                                  reply_to_message_id=target_reply_id,
                                  parse_mode="MarkdownV2"),
            self.loop,
        )
        try:
            fut.result(timeout=10)
        except Exception as e:
            logging.error(f"Telegram formatting error, falling back to plain text: {e}")
            fut_fallback = asyncio.run_coroutine_threadsafe(
                self.bot.send_message(
                    chat_id=target_chat_id, 
                    text=text, 
                    reply_to_message_id=target_reply_id
                ),
                self.loop,
            )
            try:
                fut_fallback.result(timeout=10)
            except Exception:
                pass

_channel = _TelegramChannel()

def getLastMessage():
    """Return the last processed batch window."""        
    return _channel.get_last_message()

def start_telegram(token, chat_id=None):
    """Initialize and start the Telegram bot."""
    if isinstance(token, list) and len(token) > 0:
        token = str(token[0])
    
    token = str(token).strip("\"' ")
    
    if isinstance(chat_id, list):
        chat_id = [str(item).strip("\"' ") for item in chat_id if str(item).strip("\"' ")]
    elif chat_id is not None:
        chat_id = str(chat_id).strip("\"' ")
            
    return _channel.start(token, chat_id)

def stop_telegram():
    """Stop the Telegram bot."""
    _channel.stop()

def send_message(text):
    """Send a message to the active Telegram chat."""    
    target_chat_id = _channel.chat_id
    target_reply_id = None
    m = re.match(r'^\[(-?\d+)\]\s*(?:\[(\d+)\])?\s*(.*)$', text, re.DOTALL)
    if m:
        target_chat_id = m.group(1)
        if m.group(2) and m.group(2) != "None":
            target_reply_id = int(m.group(2))

        text = m.group(3)

    # Run the async check safely in a synchronous context
    try:
        loop = asyncio.get_running_loop()
        is_blocked = loop.run_until_complete(is_category_blocked(text))
    except RuntimeError:
        is_blocked = asyncio.run(is_category_blocked(text))

    if is_blocked:
        alert_ethics_violation("send", text)
        return "Error: Refused: Unsafe response content."
        
    _channel.send_message(text, chat_id=target_chat_id, reply_to_id=target_reply_id)

def is_search_disabled():
    """Check if admin disabled searching."""
    return _channel.search_disabled

def alert_ethics_violation(tool_name, text=None):
    """Allow MeTTa to trigger an ethics alert DM to admins."""
    if _channel.loop and _channel.bot:
        for admin_id in _channel.admin_ids:
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    _channel.bot.send_message(chat_id=admin_id, text=f"🚨 Ethics Pass Triggered!\nAction Blocked: {tool_name} | With message: {text}"),
                    _channel.loop
                )
            except Exception:
                logging.error(f"Failed to send ethics alert to admin {admin_id} for tool {tool_name}")
