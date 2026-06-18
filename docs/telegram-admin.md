# Telegram Admin: History & Memory

Admin-only, button-driven management of the agent's two stores from inside Telegram:

- **Conversation history** — `memory/history.metta` (every turn the agent appends via
  `src/memory.metta` → `addToHistory`).
- **Long-term memory** — the durable ChromaDB store the agent writes to via `remember`.

An authorized admin can inspect, page through, delete, and purge entries in either
store without touching the server.

## Running it

In production the MeTTa engine launches the bot itself. To run it standalone for
testing, use the included launcher.

**1. Install dependencies** (once):

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

**2. Create a bot and get its token** from [@BotFather](https://t.me/BotFather)
(`/newbot`, or `/revoke` to rotate an existing token). Keep the token private.

**3. Get your numeric Telegram id** by messaging [@userinfobot](https://t.me/userinfobot).

**4. List yourself as an admin** in `memory/telegram_profile.yaml` (the default is
empty, so without this nobody can use the admin commands):

```yaml
admin_controls:
  admin_ids: [123456789]   # your Telegram id
```

**5. Start the bot:**

```bash
export TG_BOT_TOKEN='your-token-from-botfather'
.venv/bin/python scripts/run_telegram_bot.py
```

> `OPENAI_API_KEY` only gates the message-moderation path; the launcher sets a
> placeholder so `/history` and `/memory` work without a real key.

Then open a **direct message** with the bot and send `/start`. Press `Ctrl+C` to stop.

## Access

- Admin only, **in a direct message** with the bot (same gate as `/purge`, `/kill`).
- Admins come from `admin_controls.admin_ids`, plus group administrators
  auto-detected on startup.

## Commands

### History (`memory/history.metta`)

| Command | Action |
|---|---|
| `/history` | Open the menu (Stats / List / Purge buttons) |
| `/history_stats` | Path, entry count, file size, oldest/latest timestamps |
| `/history_list [page]` | Paged list (newest first), with a **View** button per entry |
| `/history_get <index>` | Show one full entry |
| `/history_delete <index>` | Delete one entry — asks for button confirmation |
| `/history_purge` | Wipe all history — asks for button confirmation |

### Memory (ChromaDB)

| Command | Action |
|---|---|
| `/memory` | Open the menu (Stats / List / Purge buttons) |
| `/memory_stats` | DB path, active collection, record count |
| `/memory_list [page]` | Paged list (newest first), with a **View** button per record |
| `/memory_get <id>` | Show one record |
| `/memory_delete <id>` | Delete one record — asks for button confirmation |

Everything is also reachable by tapping buttons; the slash commands are just entry
points, and `/start` shows a persistent 📜 History / 🧠 Memory keyboard. Destructive
actions (delete, purge) **always require a second button press** to confirm — there
is no silent wipe.

## What the history view shows

A stored history entry mixes several things together: the user's message, the
agent's function calls, the agent's outgoing reply, and an optional error block.
The admin view filters each entry down to the **relevant operational data only**:

- **Kept:** function calls (`query`, `pin`, `search`, …) and `ERROR_FEEDBACK`.
- **Dropped:** the user query (`HUMAN_MESSAGE`), the agent's `(send …)` reply (the
  response), and any free-text thought.

So viewing an entry shows, e.g.:

```
⚙️ Function calls:
  (query "my goals")
  (query "user context")
❗ Errors:
  (SINGLE_COMMAND_FORMAT_ERROR_NOTHING_WAS_DONE_PLEASE_FIX_AND_RETRY (query "my goals"))
```

Parsing is parenthesis-balanced and quote-aware, so parentheses inside a user
message or a quoted string are never mistaken for a function call.

## Configuration

In `memory/telegram_profile.yaml` under `admin_controls`:

```yaml
admin_controls:
  admin_ids: []           # authorized admin Telegram ids

  history_admin: true     # master switch for /history
  history_inspect: true   # stats / list / view
  history_delete: true    # delete a single entry
  history_purge: true     # purge the whole file

  memory_admin: true      # master switch for /memory
  memory_inspect: true    # stats / list / view
  memory_delete: true     # delete a single record
  purge_memory: true      # purge the whole collection (shared with /purge)
```

The ChromaDB location is read from `internal_learning.durable_memory`:

```yaml
internal_learning:
  durable_memory:
    db_path: ./chroma_db
    collection_name: memories
```

Set any flag to `false` to disable that capability (e.g. read-only access:
`history_delete: false`, `history_purge: false`).

## Design

- **`src/history_admin.py`** / **`src/memory_admin.py`** — all logic: parsing,
  stats, get/delete/purge, the relevance filter, text views, and channel-agnostic
  button specs. Pure stdlib (no aiogram/network; ChromaDB imported lazily), so each
  is unit-tested in isolation.
- **`src/config_helper.py`** — `get_history_admin_config()` /
  `get_memory_admin_config()` read the toggles and store location.
- **`channels/tg_channel.py`** — thin transport only: gate → call → render. It
  converts the button specs into aiogram keyboards and routes `hist:*` / `mem:*`
  callbacks.

History writes are atomic (temp file + `os.replace`). Deletes are verified against a
content fingerprint taken from a fresh re-read, so a concurrent engine write can
never cause the wrong entry/record to be removed.

## Tests

```bash
python -m unittest tests.test_history_admin tests.test_memory_admin
```
