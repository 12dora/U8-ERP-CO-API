"""附加数据源的公共接口：应收应付处理（process.py）、基础档案（archives.py）、总账凭证（glsrc.py）。

每个数据源是一个模块，导出 build(ctx: SourceContext) -> Source。Poller 在单据类型之后按账套轮到它们：
每个类型（config.AccountConf.source_types）一次 poll(scope)，到了 delete_scan_minutes 再一次 scan(scope)。
停止信号、发件箱积压、错误记录（poll 失败记 last_error，scan 失败记 scan_error）都由 Poller 统一处理，
与单据类型完全相同；数据源只管读桥、对比快照、提交。

数据源必须守的约定（与单据轮询一样）：
- 一轮的事件、快照、水位用一次 Store.commit_entities 提交；中途任何一页失败就抛异常，什么都不写。
- event_id 由 账套|类型|键|种类|指纹 算出（make_event），同一变化重算得到同一个值。
- 只有完整的扫描才能据此发删除事件；整轮为空而快照非空时按键逐条确认（SourceLister.load_sql），
  全部是 DocumentNotFound 才删，否则抛 BridgeContractError；
  逐条确认之间看 ctx.stopping()，收到停止信号抛 StopRequested。
- 至少每 delete_scan_minutes 提交一次 scanned=True（健康检查按它判断扫描是否停滞）；
  每轮都做完整摘要的数据源每轮都可以记。
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from u8co_events.bridge import SourceLister
from u8co_events.config import AccountConf, Config, ConfigError
from u8co_events.diff import Scope, event_id
from u8co_events.entities import EntityEvent, EntitySnap
from u8co_events.state import Store


class BridgeContractError(RuntimeError):
    """桥的响应不符合列表契约。"""


class StopRequested(Exception):
    """逐张确认途中收到停止信号：这一轮作废，不当错误记录。"""


def error_text(exc: Exception) -> str:
    code = getattr(exc, "code", "")
    message = getattr(exc, "message", "") or str(exc)
    head = f"{type(exc).__name__}"
    if code:
        head += f"[{code}]"
    return f"{head}: {message}"


@dataclass(frozen=True)
class SourceContext:
    """数据源运行所需的一切。一个账套、一个数据源一份。"""

    cfg: Config
    account: AccountConf
    store: Store
    lister: SourceLister
    now: Callable[[], datetime]
    # 收到停止信号时为真；长循环（逐条确认、翻很多页）里要看。
    stopping: Callable[[], bool]


class Source(Protocol):
    """一个附加数据源在一个账套上的轮询。scope.type 是 account.source_types(名字) 里的一个。"""

    def poll(self, scope: Scope) -> None:
        """一轮增量（或摘要对比）并提交。失败抛异常；StopRequested 表示这一轮作废、不记错误。"""
        ...

    def scan(self, scope: Scope) -> None:
        """删除扫描（按 delete_scan_minutes）。不需要单独扫描的数据源提交一次 scanned=True 的空 EntityCommit 即可。"""
        ...


SourceFactory = Callable[[SourceContext], Source]

# 数据源名 → 实现模块（模块导出 build）。vouchers、notes 走单据轮询，不在这里。
SOURCE_MODULES: Mapping[str, str] = {
    "arap_process": "u8co_events.process",
    "archives": "u8co_events.archives",
    "gl": "u8co_events.glsrc",
}


def default_factory(name: str) -> SourceFactory:
    """按名字导入数据源模块，取它的 build。模块还没有时报配置错误（服务不启动），不悄悄跳过。"""
    module_name = SOURCE_MODULES[name]
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise
        raise ConfigError(f"数据源 {name} 尚未实现（缺少模块 {module_name}）") from None
    build = getattr(module, "build", None)
    if not callable(build):
        raise ConfigError(f"数据源模块 {module_name} 没有 build(ctx)")
    return build


def make_event(scope: Scope, kind: str, prev: EntitySnap | None, curr: EntitySnap | None) -> EntityEvent:
    """附加数据源的事件，信封与单据事件相同。id、code 取自快照，ufts 填指纹（消费方当不透明值）。

    event_id = sha256(账套|类型|键|种类|指纹)，指纹取 curr（删除类取 prev）。
    """
    base = curr if curr is not None else prev
    if base is None:
        raise ValueError("事件至少要有 prev 或 curr")
    payload: dict[str, Any] = {
        "event_id": event_id(scope, base.key, kind, base.fingerprint),
        "account": scope.acc,
        "type": scope.type,
        "id": int(base.doc_id),
        "code": base.code,
        "kind": kind,
        "ufts": base.fingerprint,
        "detected_at": scope.detected_at,
        "prev": None if prev is None else dict(prev.state),
        "curr": None if curr is None else dict(curr.state),
    }
    return EntityEvent(base.key, payload)
