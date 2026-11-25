"""调试会话和回归的所有者标记。口令不进标记文件。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from co.e2e.safety import text_list
from co.e2e.settings import conf

_OWNERS = ("session", "run")


def linux_marker_path() -> Path:
    return Path(conf().report_dir) / "session.json"


def refusal_reason(linux_marker: bool, server_marker: bool) -> str:
    if not linux_marker and not server_marker:
        return ""
    return "已有会话标记。请先执行 python -m co.e2e.session close，或等看门狗还原后再 close。"


def marker_owner(local: dict | None, status: dict) -> str:
    if local and local.get("owner"):
        return str(local["owner"])
    return str(status.get("owner") or "")


def owner_conflict(local: dict | None, status: dict, caller: str) -> bool:
    # 标记属于另一个运行时，close 一律不动快照、看门狗和标记。
    owner = marker_owner(local, status)
    return bool(owner) and owner != caller


def _idle(local: dict | None, status: dict) -> bool:
    snaps = text_list(status.get("snapshots"))
    state = str(status.get("watchdog_state") or "")
    watched = state not in ("", "none")
    has_marker = local is not None or bool(status.get("marker"))
    return not has_marker and not snaps and not watched


def _verify_only(status: dict) -> bool:
    return not text_list(status.get("snapshots")) and bool(status.get("done"))


def close_plan(local: dict | None, status: dict, caller: str) -> str:
    if owner_conflict(local, status, caller):
        return "foreign"
    if _idle(local, status):
        return "already-closed"
    if _verify_only(status):
        return "verify-only"
    return "revert"


def keep_refusal(kind: str, owner: str, seedable: bool) -> str:
    # close --keep 只收可造数通道上、还有快照的 session 会话；其余一律拒绝，不动远端。
    if not seedable:
        return "本通道没有配置 account.seedable，不能 close --keep。要还原请去掉 --keep。"
    if owner != "session":
        return "close --keep 只处理所有者为 session 的会话（当前：" + (owner or "无") + "）。"
    if kind != "revert":
        return "没有可保留的会话（" + kind + "）：只有快照还在、看门狗未触发的会话才能 close --keep。"
    return ""


def keep_resumable(local: dict | None, status: dict, kind: str, have_fingerprint: bool) -> bool:
    # 上一次 close --keep 已经取消看门狗、删掉快照，只差新指纹：本机标记记着 keep，快照和看门狗任务都不在。
    if kind != "verify-only" or have_fingerprint or not local:
        return False
    if local.get("keep") != "in-progress" or marker_owner(local, status) != "session":
        return False
    return str(status.get("watchdog_state") or "none") in ("", "none")


def note_keep(report_dir: Path) -> None:
    # 取消看门狗之前在本机标记里记下 keep，中途失败后 close --keep 才能接着做。
    payload = read_linux_marker() or {"report_dir": str(report_dir), "owner": "session"}
    payload["keep"] = "in-progress"
    write_linux_marker(payload)


def markers_cleared(kind: str, revert_ok: bool, disarmed: bool, have_after: bool) -> bool:
    # 看门狗已经还原时，核对之后去掉标记，即使指纹对不上也去掉，避免卡死下一次 open。
    # keep：revert_ok 表示两个快照都已删除，have_after 表示保留后的指纹已取到。
    if kind == "verify-only":
        return disarmed
    return kind in ("revert", "keep") and revert_ok and disarmed and have_after


def read_linux_marker() -> dict | None:
    path = linux_marker_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        return data
    return None


def marker_payload(report_dir: Path, watchdog_min: int, opened_at: str, owner: str) -> dict:
    if owner not in _OWNERS:
        raise RuntimeError("所有者标记不合法")
    return {
        "report_dir": str(report_dir),
        "watchdog_min": watchdog_min,
        "opened_at": opened_at,
        "secret_path": conf().secret_file,
        "owner": owner,
    }


def write_linux_marker(payload: dict) -> None:
    path = linux_marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def remove_linux_marker() -> None:
    path = linux_marker_path().resolve()
    root = Path(conf().report_dir).resolve()
    if path.parent != root or path.name != "session.json":
        raise RuntimeError("拒绝删除会话标记以外的文件")
    if path.is_file():
        path.unlink()
