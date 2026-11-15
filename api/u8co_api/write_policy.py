"""写入策略的 API 侧：环境变量 U8CO_WRITE_POLICY_FILE，与桥的 writePolicyFile 同一份 JSON、同一套语义。

不设时什么都不做（state off）。设了文件就必须存在且有效：首次加载时缺失、无效或从未加载成功，所有写入一律
503 write_policy_unavailable（不放行）。按 reloadSeconds（缺省 2 秒）读一次文件、按内容的 SHA-256 判断是否变了
（不看修改时间和长度：保留时间戳的等长替换也要生效）：新内容无效时保留
上一份有效快照、记日志，健康检查 state 为 invalid；文件被删除时不再放行写入（missing）。

这里在访问桥之前判定第 1 到 6 步（不可用、冻结、时段、放行规则、操作员、行数与金额上限），时段按
U8CO_TIMEZONE（U8 服务器所在时区，与登录日期同一设置）判断，不看容器的 TZ。
第 7 步限额和第 8 步许可只在桥上做。读路由不受影响。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone, tzinfo
from decimal import Decimal

from u8co_api.co_bridge import default_retry_after
from u8co_api.errors import ApiError, bad_request, forbidden, unavailable
from u8co_api.write_class import AMOUNT_TYPES, LINE_OPS, WriteInfo
from u8co_api.write_policy_parse import Limits, PolicyError, Snapshot, parse

ENV = "U8CO_WRITE_POLICY_FILE"
OFF = "off"
OK = "ok"
INVALID = "invalid"
MISSING = "missing"
_SIZE_LIMIT = 1024 * 1024
_GONE = "-"
_LOG = logging.getLogger("u8co.api")


class WritePolicy:
    """一个策略文件。current / state 在访问时按 reloadSeconds 节流地看一次文件，变了就重读。线程安全。"""

    def __init__(
        self,
        path: str,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.path = path
        self._clock = clock
        self._now = now
        self._lock = threading.Lock()
        self._current: Snapshot | None = None
        self._state = MISSING
        # 上次看到的文件内容签名（SHA-256 十六进制）；相同则不重新解析。文件不存在时为 _GONE。
        self._seen: str | None = None
        self._next = 0.0
        with self._lock:
            self._poll()

    @property
    def current(self) -> Snapshot | None:
        self._refresh()
        return self._current

    @property
    def state(self) -> str:
        self._refresh()
        return self._state

    def local_now(self) -> datetime:
        """U8CO_TIMEZONE 的当地时间（不带时区；没给时区时用本机时间），时段和 denyDates 按它判断。"""
        return self._now()

    def check(self, info: WriteInfo) -> ApiError | None:
        """第 1 到 6 步；None 表示放行。"""
        self._refresh()
        return decide(self._current, info, self.local_now())

    def health(self) -> dict:
        self._refresh()
        return health_of(self._state, self._current, self.local_now())

    def _refresh(self) -> None:
        if self._clock() < self._next:
            return
        with self._lock:
            if self._clock() >= self._next:
                self._poll()

    def _poll(self) -> None:
        # 只在锁内调用。
        snap = self._current
        self._next = self._clock() + (2 if snap is None else snap.reload_seconds)
        try:
            with open(self.path, "rb") as handle:
                raw = handle.read(_SIZE_LIMIT + 1)
        except FileNotFoundError:
            self._missing()
            return
        except OSError:
            # 正在被改写或读不到（权限）：状态不变，下一轮再读。
            return
        seen = hashlib.sha256(raw).hexdigest()
        if seen == self._seen:
            return
        self._seen = seen
        self._apply(raw)

    def _missing(self) -> None:
        if self._seen != _GONE:
            _LOG.warning("写入策略文件不存在，所有写入都会被拒绝: %s", self.path)
        self._current = None
        self._state = MISSING
        self._seen = _GONE

    def _apply(self, raw: bytes) -> None:
        try:
            snap = parse(_decode(raw), datetime.now(timezone.utc))
        except PolicyError as exc:
            self._state = INVALID
            _LOG.warning("写入策略无效，保留上一份有效策略: %s", str(exc)[:300])
            return
        self._current = snap
        self._state = OK
        self._next = self._clock() + snap.reload_seconds
        _LOG.info("写入策略已加载: version %d", snap.version)


def _decode(raw: bytes) -> str:
    if len(raw) > _SIZE_LIMIT:
        raise PolicyError("文件超过 1 MiB")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PolicyError("不是 UTF-8 文本") from exc


def load_write_policy(path: str, zone: tzinfo | None = None) -> WritePolicy | None:
    """U8CO_WRITE_POLICY_FILE 为空时返回 None（不启用）。时段按 U8CO_TIMEZONE（U8 服务器时区）判断，不看容器时区。"""
    if not path:
        return None
    if zone is None:
        return WritePolicy(path)
    return WritePolicy(path, now=lambda: datetime.now(zone).replace(tzinfo=None))


def decide(snap: Snapshot | None, info: WriteInfo, now_local: datetime) -> ApiError | None:
    """决策顺序（先失败的一步为准），与桥 WritePolicyEval.Check 相同。snap 为 None 表示策略不可用。"""
    if snap is None:
        return _retryable(unavailable("写入策略不可用", "write_policy_unavailable"))
    if snap.is_frozen(info.acc):
        reason = "：" + snap.freeze_reason if snap.freeze_reason else ""
        return _retryable(unavailable("写入已冻结" + reason, "write_frozen"))
    if not snap.window_open(now_local):
        return _retryable(unavailable("当前时段不允许写入", "write_window"))
    account = snap.account(info.acc)
    allowed = snap.unlisted_allow if account is None else account.allows(info.type, info.op)
    if not allowed:
        error = forbidden("该账套不允许此写入", "write_not_allowed")
        error.detail = {"type": info.type, "op": info.op}
        return error
    if account is not None and not account.operators.permits(info.operator):
        return forbidden("该操作员不能在此账套写入", "operator_not_allowed")
    return _limits(snap.limits(info.acc), info)


def _limits(limits: Limits, info: WriteInfo) -> ApiError | None:
    # 第 6 步：行数上限（新增、修改、生单）；金额上限只对收付款类单据，按绝对值比较。
    if limits.max_lines > 0 and info.lines > limits.max_lines and info.op in LINE_OPS:
        error = bad_request("行数超过上限", "write_limit", field="lines")
        error.detail = {"max": limits.max_lines, "actual": info.lines}
        return error
    if limits.max_amount <= 0 or info.type not in AMOUNT_TYPES:
        return None
    if info.amount_bad:
        # 金额或汇率给了但解析不了：按超过上限拒绝（与桥相同），不能靠写入方认、这里不认的写法绕过上限。
        error = bad_request("金额或汇率无法识别，按超过上限处理", "write_limit")
        error.detail = {"max": _plain(limits.max_amount)}
        return error
    amount = info.amount
    if amount is not None and abs(amount) > limits.max_amount:
        error = bad_request("金额超过上限", "write_limit")
        error.detail = {"max": _plain(limits.max_amount), "actual": _plain(amount)}
        return error
    return None


def _plain(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def _retryable(error: ApiError) -> ApiError:
    error.retry_after = default_retry_after(error.code)
    return error


def health_of(state: str, snap: Snapshot | None, now_local: datetime) -> dict:
    """健康检查的 write_policy 对象，形状与桥相同：
    {state, version, loaded_at, freeze:{global, accounts}, window_open}。"""
    body: dict = {"state": state}
    if state == OFF:
        return body
    body["version"] = None if snap is None else snap.version
    body["loaded_at"] = None if snap is None else snap.loaded_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    body["freeze"] = {
        "global": snap is not None and snap.freeze_global,
        "accounts": [] if snap is None else list(snap.freeze_accounts),
    }
    body["window_open"] = snap is not None and snap.window_open(now_local)
    return body
