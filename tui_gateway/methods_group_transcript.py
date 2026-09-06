"""Verbatim per-room transcript for Desktop group chats (the room "note taker").

The Desktop keeps only a bounded window of each room's log and the ui_meta mirror keeps
even less, so nothing durable holds a room's full history. The renderer plugin calls
``group.transcript.append`` from the one place it appends a room entry (user sends,
member replies, late-delivered stranded replies alike); this module appends the entry,
byte-for-byte, to ``<room_log.dir>/<room-slug>.md`` (default ``<HERMES_HOME>/room-logs``).

Design rules:
- Never paraphrase, never reflow: the entry text is written exactly as posted. Metadata
  (timestamp, speaker, entry id, thread) lives on the heading line and in an HTML comment
  so the body stays verbatim.
- Idempotent per entry id: a retried or double-delivered append does not duplicate.
- Never raises into the room: every failure is a JSON-RPC error the caller ignores.
- Append-only, one ``os.write`` per entry with ``O_APPEND`` so concurrent writers from
  several profile backends cannot interleave inside one entry.
"""

from __future__ import annotations

# NOTE: ``bind_module`` publishes every function here onto server.py's globals (rebound to them), so:
#  - module-level imports are NOT visible inside them: each function imports what it needs locally;
#  - names must not collide with server.py globals (a bare ``_hermes_home`` once clobbered the
#    server's own): every helper carries the ``_gt_``/``group_transcript_`` prefix.
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method


def _gt_hermes_home() -> "Path":
    """The hermes ROOT, never a profile home. Profile backends run with
    ``HERMES_HOME=<root>/profiles/<name>``; the renderer sends the append to whichever
    profile's backend is active, so anchoring on the current home would scatter one room's
    record across profile folders (it did: first live post landed under profiles/orchestrator)."""
    import os
    from pathlib import Path
    try:
        from hermes_constants import get_default_hermes_root
        return Path(get_default_hermes_root())
    except Exception:
        env_path = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
        return env_path.parent.parent if env_path.parent.name == "profiles" else env_path


def _gt_room_log_dir() -> "Path":
    """``room_log.dir`` from config.yaml (absolute or relative to HERMES_HOME), else ``<HERMES_HOME>/room-logs``."""
    from pathlib import Path
    configured = None
    try:
        from hermes_cli.config import load_config_readonly
        configured = (load_config_readonly().get("room_log") or {}).get("dir")
    except Exception:
        configured = None
    if configured:
        path = Path(str(configured)).expanduser()
        return path if path.is_absolute() else _gt_hermes_home() / path
    return _gt_hermes_home() / "room-logs"


def _gt_room_slug(group: str, room_id) -> str:
    """Stable file stem: display name (readable) plus roomId (immutable) when the room has one."""
    import re
    _SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")
    base = _SLUG_RE.sub("-", str(group or "room").strip()).strip("-") or "room"
    if room_id:
        return f"{base}--{_SLUG_RE.sub('-', str(room_id))}"
    return base


def group_transcript_path(group: str, room_id=None) -> "Path":
    return _gt_room_log_dir() / f"{_gt_room_slug(group, room_id)}.md"


def _gt_speaker(entry: dict) -> str:
    frm = entry.get("from") if isinstance(entry.get("from"), dict) else {}
    kind = str(frm.get("kind") or "")
    name = str(frm.get("name") or "").strip()
    if kind == "user":
        return name or "You (user)"
    source = str(frm.get("source") or "").strip()
    label = name or kind or "member"
    return f"{label} @{source}" if source else label


def _gt_stamp(at_ms) -> str:
    import datetime as _dt
    try:
        return _dt.datetime.fromtimestamp(float(at_ms) / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _gt_already_written(path, entry_id: str) -> bool:
    _TAIL_SCAN_BYTES = 256 * 1024
    if not entry_id or not path.exists():
        return False
    needle = f"id={entry_id} ".encode("utf-8")
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            fh.seek(max(0, size - _TAIL_SCAN_BYTES))
            return needle in fh.read()
    except OSError:
        return False


def _gt_render(group: str, entry: dict) -> str:
    entry_id = str(entry.get("id") or "")
    thread = str(entry.get("thread") or "legacy")
    text = entry.get("text")
    text = "" if text is None else str(text)
    images = entry.get("images") if isinstance(entry.get("images"), list) else []
    image_lines = "".join(
        f"[attached image: {str((img or {}).get('name') or 'image')}]\n" for img in images if isinstance(img, dict))
    return (
        f"### {_gt_stamp(entry.get('at'))} — {_gt_speaker(entry)}  <!-- id={entry_id} thread={thread} -->\n"
        f"{text}\n{image_lines}\n"
    )


def _gt_header(group: str, room_id) -> str:
    return (
        f"# Room transcript — {group}\n\n"
        f"Verbatim, append-only record written by the gateway at post time "
        f"(`group.transcript.append`). Room id: `{room_id or 'legacy'}`. "
        f"Entry bodies are exactly as posted; headings carry time, speaker, entry id and thread.\n\n"
    )


def group_transcript_append_entry(group: str, room_id, entry: dict) -> dict:
    import os
    path = group_transcript_path(group, room_id)
    entry_id = str(entry.get("id") or "")
    if _gt_already_written(path, entry_id):
        return {"path": str(path), "appended": False, "duplicate": True}
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists() or path.stat().st_size == 0
    payload = ((_gt_header(group, room_id) if fresh else "") + _gt_render(group, entry)).encode("utf-8")
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    return {"path": str(path), "appended": True, "duplicate": False}


@method("group.transcript.append")
def _group_transcript_append(rid, params):
    group = str((params or {}).get("group") or "").strip()
    entry = (params or {}).get("entry")
    if not group or not isinstance(entry, dict):
        return _err(rid, -32602, "group and entry required")
    try:
        return _ok(rid, group_transcript_append_entry(group, (params or {}).get("room_id") or None, entry))
    except OSError as exc:
        return _err(rid, -32000, f"transcript append failed: {exc}")


@method("group.transcript.path")
def _group_transcript_path(rid, params):
    group = str((params or {}).get("group") or "").strip()
    if not group:
        return _err(rid, -32602, "group required")
    path = group_transcript_path(group, (params or {}).get("room_id") or None)
    return _ok(rid, {"path": str(path), "exists": path.exists()})


def register(server):
    bind_module(globals(), server)
