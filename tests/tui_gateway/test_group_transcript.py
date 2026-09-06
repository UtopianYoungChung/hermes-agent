"""group.transcript.append / group.transcript.path — the verbatim room note taker."""
from __future__ import annotations

import tui_gateway.server as srv
import tui_gateway.methods_group_transcript as gt


def _call(method: str, params: dict) -> dict:
    return srv._methods[method](1, params)


def _use_dir(monkeypatch, directory):
    """bind_module rebinds the handlers onto server.py's globals, so patch BOTH namespaces."""
    monkeypatch.setattr(gt, "_gt_room_log_dir", lambda: directory)
    monkeypatch.setattr(srv, "_gt_room_log_dir", lambda: directory)


def _entry(**over):
    base = {"id": "e1", "at": 1788715591000, "thread": "t1",
            "from": {"kind": "member", "name": "research-methods-fellow"},
            "text": "Line one.\n\n  indented > not a quote\n```\ncode\n```"}
    base.update(over)
    return base


def test_append_writes_verbatim_with_header(tmp_path, monkeypatch):
    _use_dir(monkeypatch, tmp_path)
    res = _call("group.transcript.append", {"group": "Think Tank", "room_id": "r1", "entry": _entry()})["result"]
    assert res["appended"] is True and res["duplicate"] is False
    path = tmp_path / "Think-Tank--r1.md"
    assert res["path"] == str(path)
    body = path.read_text(encoding="utf-8")
    assert body.startswith("# Room transcript — Think Tank\n")
    # body text is byte-identical, including blank lines, indentation and fences
    assert "Line one.\n\n  indented > not a quote\n```\ncode\n```\n" in body
    assert "<!-- id=e1 thread=t1 -->" in body
    assert "— research-methods-fellow" in body


def test_append_is_idempotent_per_entry_id(tmp_path, monkeypatch):
    _use_dir(monkeypatch, tmp_path)
    p = {"group": "Think Tank", "room_id": "r1", "entry": _entry()}
    _call("group.transcript.append", p)
    again = _call("group.transcript.append", p)["result"]
    assert again["appended"] is False and again["duplicate"] is True
    assert (tmp_path / "Think-Tank--r1.md").read_text(encoding="utf-8").count("id=e1 ") == 1


def test_user_and_remote_member_speakers(tmp_path, monkeypatch):
    _use_dir(monkeypatch, tmp_path)
    _call("group.transcript.append", {"group": "Think Tank", "entry": _entry(id="u1", **{"from": {"kind": "user"}}, text="/new")})
    _call("group.transcript.append", {"group": "Think Tank", "entry": _entry(
        id="m2", **{"from": {"kind": "member", "name": "impl", "source": "laptop"}}, text="ok", images=[{"name": "shot.png"}])})
    body = (tmp_path / "Think-Tank.md").read_text(encoding="utf-8")  # legacy room: no roomId in the stem
    assert "— You (user)  <!-- id=u1" in body and "\n/new\n" in body
    assert "— impl @laptop  <!-- id=m2" in body and "[attached image: shot.png]" in body


def test_bad_params_are_rpc_errors_not_exceptions(tmp_path, monkeypatch):
    _use_dir(monkeypatch, tmp_path)
    assert "error" in _call("group.transcript.append", {"group": "", "entry": _entry()})
    assert "error" in _call("group.transcript.append", {"group": "Think Tank", "entry": "nope"})
    assert "error" in _call("group.transcript.path", {})


def test_path_reports_existence(tmp_path, monkeypatch):
    _use_dir(monkeypatch, tmp_path)
    before = _call("group.transcript.path", {"group": "Think Tank", "room_id": "r1"})["result"]
    assert before["exists"] is False
    _call("group.transcript.append", {"group": "Think Tank", "room_id": "r1", "entry": _entry()})
    after = _call("group.transcript.path", {"group": "Think Tank", "room_id": "r1"})["result"]
    assert after["exists"] is True and after["path"] == before["path"]


def test_dir_override_relative_to_home(tmp_path, monkeypatch):
    monkeypatch.setattr(gt, "_gt_hermes_home", lambda: tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda: {"room_log": {"dir": "research/room-logs"}})
    assert gt._gt_room_log_dir() == tmp_path / "research" / "room-logs"
    monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda: {})
    assert gt._gt_room_log_dir() == tmp_path / "room-logs"


def test_home_is_the_hermes_root_not_the_profile_home(tmp_path, monkeypatch):
    """A profile backend (HERMES_HOME=<root>/profiles/<name>) must write under <root>/room-logs."""
    root = tmp_path / "hermes-root"
    (root / "profiles" / "orchestrator").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root / "profiles" / "orchestrator"))
    import hermes_constants
    monkeypatch.setattr(hermes_constants, "_default_hermes_root_memo", None, raising=False)
    monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda: {})
    assert gt._gt_hermes_home() == root
    assert gt._gt_room_log_dir() == root / "room-logs"

