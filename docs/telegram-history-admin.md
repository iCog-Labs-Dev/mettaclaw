# Telegram History Admin

Admin-only, button-driven management of the agent's conversation history
(`memory/history.metta`) from inside Telegram.

The agent appends every human turn and its own response to `memory/history.metta`
(see `src/memory.metta` → `addToHistory`). This feature lets an authorized admin
inspect, page through, delete, and purge those entries without touching the server.

## Access

- Admin only, **in a direct message** with the bot (same gate as `/purge`, `/kill`).
- Admins come from `admin_controls.admin_ids` in `memory/telegram_profile.yaml`,
  plus group administrators auto-detected on startup.

## Commands

| Command | Action |
|---|---|
| `/history` | Open the menu (Stats / List / Purge buttons) |
| `/history_stats` | Path, entry count, file size, oldest/latest timestamps |
| `/history_list [page]` | Paged list (newest first), with a **View** button per entry |
| `/history_get <index>` | Show one full entry |
| `/history_delete <index>` | Delete one entry — asks for button confirmation |
| `/history_purge` | Wipe all history — asks for button confirmation |

Everything is also reachable by tapping buttons; the slash commands are just entry
points. Destructive actions (delete, purge) **always require a second button press**
to confirm — there is no silent wipe.

## Configuration

In `memory/telegram_profile.yaml` under `admin_controls`:

```yaml
admin_controls:
  history_admin: true     # master switch
  history_inspect: true   # stats / list / view
  history_delete: true    # delete a single entry
  history_purge: true     # purge the whole file
```

Set any flag to `false` to disable that capability (e.g. read-only access:
`history_delete: false`, `history_purge: false`).

## Design

- **`src/history_admin.py`** — all logic: parsing, stats, get/delete/purge,
  text views, and channel-agnostic button specs. Pure stdlib, no aiogram/network,
  so it is unit-tested in isolation (`tests/test_history_admin.py`).
- **`src/config_helper.py`** — `get_history_admin_config()` reads the toggles.
- **`channels/tg_channel.py`** — thin transport only: gate → call → render. It
  converts the button specs into aiogram keyboards and routes `hist:*` callbacks.

Writes are atomic (temp file + `os.replace`) so an interrupted delete/purge can
never leave a half-written history file.

## Tests

```bash
python -m unittest tests.test_history_admin
```
