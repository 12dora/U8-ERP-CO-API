"""服务器状态、还原窗口和 MsgCenter 只读核对。"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from co.e2e.remote import Remote, RemoteError, RemoteResult
from co.e2e.runfiles import drop_remote_out, run_tag, status_tagged, tagged, warn_old_scripts
from co.e2e.safety import msgcenter_warning, start_block_reason, watchdog_running, window_block_reason
from co.e2e.settings import conf


def _inrevert_names() -> tuple[str, ...]:
    return tuple("fp-inrevert-" + name + ".json" for name in conf().databases)


def clear_local_inrevert(report_dir: Path) -> None:
    root = report_dir.resolve()
    names = _inrevert_names()
    for name in names:
        path = (root / name).resolve()
        if path.parent != root or path.name not in names:
            raise RuntimeError("拒绝删除名单外的指纹")
        if path.is_file():
            path.unlink()


def _log(ctx, name: str, result: RemoteResult) -> None:
    text = ctx.scrub(result.out)
    (ctx.report_dir / f"{len(ctx.steps):02d}-{name}.log").write_text(text, encoding="utf-8")
    ctx.add_step(name, result.code == 0)


def fetch_status(remote: Remote, dest: Path, ensure: bool = True) -> dict:
    # ensure=False 给 call 和 status 用：它们会和同一会话里的其他命令并行，不重锁目录（锁目录会向下传播权限）。
    # 服务器脚本自己仍会核对目录已锁好（common.ps1 Assert-RootLocked）。
    if ensure:
        made = remote.ensure_dirs()
        if made.code != 0:
            raise RuntimeError("创建目录失败")
    tag = run_tag()
    name = tagged(tag, "session-status.json")
    result = remote.run_script("session-status.ps1", {"Tag": tag}, 180)
    if result.code != 0:
        raise RuntimeError((result.out or "读会话状态失败")[:300])
    data = _download_status(remote, dest, name if status_tagged(result.out, tag) else "")
    if not isinstance(data, dict):
        raise RuntimeError("会话状态不是对象")
    problem = identity_problem(data)
    if problem:
        raise RuntimeError(problem)
    return data


def _download_status(remote: Remote, dest: Path, name: str) -> object:
    # name 为空：服务器上是旧版脚本，没认 -Tag，状态写在不带标记的旧文件里（runfiles.status_tagged）。
    if name == "":
        warn_old_scripts()
        path = dest / tagged(run_tag(), "session-status.json")
        try:
            remote.download(conf().remote("out", "session-status.json"), path)
            return json.loads(path.read_text(encoding="utf-8"))
        finally:
            path.unlink(missing_ok=True)
    path = dest / name
    try:
        remote.download(conf().remote("out", name), path)
        return json.loads(path.read_text(encoding="utf-8"))
    finally:
        path.unlink(missing_ok=True)
        drop_remote_out(remote, name)


def identity_problem(status: dict) -> str:
    # 服务器上的 e2e.json 是 open 时上传的那一份。本机配置换了账套或快照名，就不能拿它去还原或调用。
    cfg = conf()
    got = status.get("config")
    if not isinstance(got, dict):
        return "服务器会话状态缺少配置标识，请先重新上传脚本（run 或 session open）"
    want = {"account": cfg.account, "data_db": cfg.data_db, "meta_db": cfg.meta_db, "snap_suffix": cfg.snap_suffix}
    want["revert_task"] = cfg.revert_task
    for key, value in want.items():
        if str(got.get(key) or "") != value:
            return "服务器上的 e2e.json 与本机配置不一致（" + key + "），拒绝继续"
    return ""


def poll_until_idle(remote: Remote, dest: Path) -> dict:
    # 第一次立即查、并重锁目录；之后只在两次查询之间等 10 秒，目录已锁好，不再重锁（每次约 6 秒）。
    deadline = time.time() + 7200
    ensure = True
    while True:
        status = fetch_status(remote, dest, ensure)
        ensure = False
        if not watchdog_running(status):
            return status
        if time.time() >= deadline:
            raise RuntimeError("看门狗仍在还原")
        time.sleep(10)


def assert_window(remote: Remote, report: Path, ensure: bool = False) -> None:
    reason = window_block_reason(fetch_status(remote, report, ensure), datetime.now(ZoneInfo(conf().timezone)))
    if reason:
        raise RuntimeError(reason)
    result = remote.run_script("session-gate.ps1", None, 60)
    if result.code != 0:
        raise RuntimeError((result.out or "会话窗口已关闭")[:300])


def load_remote_json(ctx, remote_name: str, local_name: str) -> dict | None:
    local = ctx.report_dir / local_name
    try:
        ctx.remote.download(conf().remote("out", remote_name), local)
        data = json.loads(local.read_text(encoding="utf-8"))
    except (OSError, RemoteError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        return data
    return None


def wait_until_idle(ctx) -> None:
    status = poll_until_idle(ctx.remote, ctx.report_dir)
    _log(ctx, "watchdog-wait", RemoteResult(0, str(status.get("watchdog_state") or "none")))


def start_if_idle(ctx) -> bool:
    try:
        status = fetch_status(ctx.remote, ctx.report_dir)
    except (RuntimeError, RemoteError, OSError, json.JSONDecodeError) as exc:
        ctx.fail("无法确认远端是否仍在还原，不启动服务 " + str(exc)[:120])
        return False
    reason = start_block_reason(status)
    if reason:
        ctx.fail(reason)
        return False
    from co.e2e.flow import start_service

    start_service(ctx)
    return True


_MSG_WHEN = {"open": "打开时", "close": "结束时"}


def _store_msg(ctx, label: str, data: dict | None) -> None:
    # 只记警告，不判失败：U8 消息中心常驻打开 MsgCenter.db，读不到或哈希变化都不代表本次写入有问题。
    # msgcenter-hash.ps1 共享读重试后仍读不到时写 busy=true（被占用或 denied=true 没有权限）；
    # in_use=true 表示读取时文件被锁、重试后才读到。
    if data and data.get("busy"):
        why = "没有权限读取 MsgCenter.db " if data.get("denied") else "MsgCenter.db 正被占用，重试后仍没能读取 "
        reason = str(data.get("reason") or "").strip()[:160]
        ctx.warnings.append(("警告：" + _MSG_WHEN[label] + why + reason).strip())
        if label == "open":
            ctx.msg_open = None
        else:
            ctx.msg_close = None
        return
    if label == "open":
        ctx.msg_open = data
        if data is None:
            ctx.warnings.append("警告：打开时没能读取 MsgCenter.db")
        return
    ctx.msg_close = data
    note = msgcenter_warning(ctx.msg_open, data)
    if note and _msg_in_use(ctx.msg_open, data):
        note += "（读取时文件正被 U8 占用，读取期间的写入会让哈希不同）"
    if note:
        ctx.warnings.append(note)


def _msg_in_use(opened: dict | None, closed: dict | None) -> bool:
    return any(bool(doc and doc.get("in_use")) for doc in (opened, closed))


def capture_msgcenter(ctx, label: str) -> None:
    # 只读核对 <U8 安装目录>\MessageCenter\MsgCenter.db，不写 U8 安装目录。
    try:
        result = ctx.remote.run_script("msgcenter-hash.ps1", None, 120)
    except RemoteError as exc:
        _log(ctx, "msgcenter-" + label, RemoteResult(1, exc.output))
        _store_msg(ctx, label, None)
        return
    _log(ctx, "msgcenter-" + label, result)
    data = None
    if result.code == 0:
        data = load_remote_json(ctx, "msgcenter.json", "msgcenter-" + label + ".json")
    _store_msg(ctx, label, data)
