"""Admin inspection and management of the agent's long-term (ChromaDB) memory.

The agent stores durable, self-selected facts via ``remember`` into a ChromaDB
collection (see ``src/memory.metta`` -> ``remember`` / ``lib_chromadb``). This
module gives Telegram admins a button-driven way to list, view, delete, and
purge those records.

Design mirrors ``src/history_admin.py``: the record/view/button logic is pure and
operates on a *duck-typed* Chroma collection (``count`` / ``get`` / ``delete`` /
``name``), so it is unit-testable with a fake collection and never imports
chromadb itself. Only ``open_collection`` touches chromadb, lazily.
"""

import hashlib
import os
from typing import List, Optional, Tuple

TELEGRAM_SAFE_LEN = 3900
DEFAULT_PAGE_SIZE = 5
MAX_FETCH = 50
PREVIEW_WIDTH = 90

CB_PREFIX = "mem"


# ---------------------------------------------------------------------------
# ChromaDB access (the only chromadb-dependent part)
# ---------------------------------------------------------------------------

def open_collection(db_path: str, collection_name: str):
    """Open the best-matching persistent collection. Lazy-imports chromadb.

    Picks, among (configured name, "memories", "memory"), the existing
    collection with the most records; otherwise creates the configured one.
    Returns (client, collection). Raises RuntimeError if chromadb is missing.
    """
    try:
        import chromadb
    except ImportError as e:  # pragma: no cover - depends on runtime env
        raise RuntimeError("chromadb is not installed in this environment") from e

    client = chromadb.PersistentClient(path=db_path)

    candidates, seen = [], set()
    for name in (collection_name, "memories", "memory"):
        if name and name not in seen:
            seen.add(name)
            candidates.append(name)

    existing = {c.name: c for c in client.list_collections()}
    best, best_count = None, -1
    for name in candidates:
        col = existing.get(name)
        if col is not None and col.count() > best_count:
            best, best_count = name, col.count()

    name = best or collection_name or "memories"
    return client, client.get_or_create_collection(name=name)


# ---------------------------------------------------------------------------
# Record operations (pure; operate on a duck-typed collection)
# ---------------------------------------------------------------------------

def fingerprint(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:8]


def _record(rec_id, doc, meta) -> dict:
    meta = meta or {}
    ts = meta.get("timestamp") or meta.get("time") or "n/a"
    return {"id": rec_id, "timestamp": ts, "doc": doc or ""}


def recent_records(collection, max_fetch: int = MAX_FETCH) -> Tuple[List[dict], int]:
    """Return (newest-first records, total_count). Caps the fetch window."""
    total = collection.count()
    if total == 0:
        return [], 0
    limit = min(max_fetch, total)
    offset = max(0, total - limit)
    rows = collection.get(limit=limit, offset=offset, include=["documents", "metadatas"])
    ids = rows.get("ids") or []
    docs = rows.get("documents") or []
    metas = rows.get("metadatas") or []
    recs = [
        _record(rid, docs[i] if i < len(docs) else "", metas[i] if i < len(metas) else {})
        for i, rid in enumerate(ids)
    ]
    recs.reverse()  # newest first
    return recs, total


def get_record(collection, rec_id: str) -> Optional[dict]:
    rows = collection.get(ids=[rec_id], include=["documents", "metadatas"])
    ids = rows.get("ids") or []
    if not ids:
        return None
    docs = rows.get("documents") or [""]
    metas = rows.get("metadatas") or [{}]
    return _record(ids[0], docs[0] if docs else "", metas[0] if metas else {})


def delete_record(collection, rec_id: str) -> bool:
    if not (collection.get(ids=[rec_id]).get("ids")):
        return False
    collection.delete(ids=[rec_id])
    return True


def stats(client, collection) -> dict:
    names = [c.name for c in client.list_collections()]
    return {
        "collection": collection.name,
        "count": collection.count(),
        "collections": names,
    }


def purge(client, collection) -> str:
    name = collection.name
    client.delete_collection(name)
    client.get_or_create_collection(name=name)
    return name


# ---------------------------------------------------------------------------
# Views (text)
# ---------------------------------------------------------------------------

def _truncate(text: str, limit: int = TELEGRAM_SAFE_LEN) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _preview(doc: str, width: int = PREVIEW_WIDTH) -> str:
    flat = " ".join((doc or "").split())
    return flat if len(flat) <= width else flat[: width - 3] + "..."


def page_count(total: int, page_size: int = DEFAULT_PAGE_SIZE) -> int:
    return 1 if total <= 0 else (total + page_size - 1) // page_size


def format_stats(client, collection, db_path: str) -> str:
    s = stats(client, collection)
    return "\n".join([
        "🧠 Long-term Memory (ChromaDB)",
        f"DB path: {db_path}",
        f"Active collection: {s['collection']}",
        f"Record count: {s['count']}",
        f"Collections: {', '.join(s['collections']) if s['collections'] else 'none'}",
    ])


def format_record(rec: dict, pos: int) -> str:
    return _truncate(
        f"🧠 Memory #{pos + 1}\nID: {rec['id']}\nTimestamp: {rec['timestamp']}\n\n{rec['doc']}"
    )


def format_list_page(recs: List[dict], total: int, page: int,
                     page_size: int = DEFAULT_PAGE_SIZE) -> List[dict]:
    """Return the slice of newest-first records for ``page`` (no text here;
    the caller renders header + buttons). Positions are global within ``recs``."""
    start = (page - 1) * page_size
    return recs[start:start + page_size]


def list_page_text(page_recs: List[dict], start_pos: int, total: int,
                   page: int, page_size: int = DEFAULT_PAGE_SIZE) -> str:
    if total == 0:
        return "ℹ️ No memory records found."
    lines = [f"🧠 Memory — page {page}/{page_count(total, page_size)} ({total} records)"]
    for i, r in enumerate(page_recs):
        pos = start_pos + i
        lines.append(f"#{pos + 1} · {r['timestamp']}\n  {_preview(r['doc'])}")
    return _truncate("\n".join(lines))


# ---------------------------------------------------------------------------
# Button specs (channel-agnostic) — callback data < 64 bytes
# ---------------------------------------------------------------------------

def cb(action: str, arg: Optional[object] = None) -> str:
    return f"{CB_PREFIX}:{action}" if arg is None else f"{CB_PREFIX}:{action}:{arg}"


def parse_callback(data: str) -> Optional[Tuple[str, Optional[str]]]:
    if not data or not data.startswith(CB_PREFIX + ":"):
        return None
    parts = data.split(":", 2)
    return (parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else None)


def menu_buttons() -> List[List[Tuple[str, str]]]:
    return [
        [("📊 Stats", cb("stats")), ("🧠 List", cb("list", 1))],
        [("🗑 Purge all", cb("purgeask"))],
    ]


def list_page_buttons(page_recs: List[dict], start_pos: int, page: int, total: int,
                      page_size: int = DEFAULT_PAGE_SIZE) -> List[List[Tuple[str, str]]]:
    rows: List[List[Tuple[str, str]]] = []
    for i, _r in enumerate(page_recs):
        pos = start_pos + i
        rows.append([(f"🔍 View #{pos + 1}", cb("view", pos))])
    pages = page_count(total, page_size)
    nav: List[Tuple[str, str]] = []
    if page > 1:
        nav.append(("◀️ Prev", cb("list", page - 1)))
    if page < pages:
        nav.append(("Next ▶️", cb("list", page + 1)))
    if nav:
        rows.append(nav)
    rows.append([("🔙 Menu", cb("menu"))])
    return rows


def record_buttons(pos: int) -> List[List[Tuple[str, str]]]:
    return [
        [("🗑 Delete this", cb("delask", pos))],
        [("🧠 List", cb("list", 1)), ("🔙 Menu", cb("menu"))],
    ]


def delete_confirm_buttons(pos: int, fp: str) -> List[List[Tuple[str, str]]]:
    return [[(f"✅ Yes, delete #{pos + 1}", cb("del", f"{pos}:{fp}")), ("❌ Cancel", cb("view", pos))]]


def purge_confirm_buttons() -> List[List[Tuple[str, str]]]:
    return [[("✅ Yes, purge ALL", cb("purge")), ("❌ Cancel", cb("menu"))]]


# ---------------------------------------------------------------------------
# Concurrency-safe resolution: position -> record, verified by fingerprint
# ---------------------------------------------------------------------------

def resolve_record(recs: List[dict], pos: int, fp: Optional[str] = None) -> Optional[dict]:
    """Resolve a record by position, preferring an exact fingerprint match so a
    record list that shifted under a concurrent write can't select the wrong one."""
    if fp is None:
        return recs[pos] if 0 <= pos < len(recs) else None
    if 0 <= pos < len(recs) and fingerprint(recs[pos]["doc"]) == fp:
        return recs[pos]
    for r in recs:
        if fingerprint(r["doc"]) == fp:
            return r
    return None


def resolve_db_path(db_path: str, repo_root: str) -> str:
    """Resolve a configured db_path to an absolute path under the repo root."""
    if not db_path:
        db_path = "./chroma_db"
    if os.path.isabs(db_path):
        return db_path
    return os.path.abspath(os.path.join(repo_root, db_path))
