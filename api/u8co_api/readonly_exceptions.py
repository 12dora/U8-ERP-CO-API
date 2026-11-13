"""只读例外（U8CO_READONLY_EXCEPTIONS）：只读账套上允许穿过闸门的精确写入。

JSON 数组，每项 accounts / operators（或 callers）/ type / op，archives 的 create、delete 另有 archives。
operators 与 callers 恰好写一个且非空，两个都写或都不写让进程起不来。callers 是 API 信任项的 name（调用系统，
例如 CRM 用的那一条）：只能用于 type=vouchers 且 op 是登记表里单据类型不固定的行（create、update、close、verify、
generate），archives、head_fix、so_contract 只能写 operators。callers 行命中 = 账套在 accounts、请求的已验签信任项
name 在 callers 里、路由 / 单据类型 / 来源规则与 operators 行完全相同；这时任何操作员都放行（操作员仍以自己的口令
登录 U8，受 U8 自身权限和信任项 operators.allow / deny 约束）。审计 read_only_match 记 operator 或 caller。
type 只能是 archives 或 vouchers。
type=archives：op 只能是 customer_person、customer_fields、customer_address、customer_address_add、
customer_address_delete（客户主数据的受控 SQL 写入）、create 或 delete；请求路径必须恰好是 archives/<op>。
op=create、delete 必须写 archives：非空的档案种类列表，每项都在代码里该 op 的可开放集合内
（create：district_class、customer；delete：只有 district_class），放行时请求体的 archive 须在列表里；
其它 op 不能写 archives。地区分类的 delete 与 create 成对开放，用来回滚刚新增的档案；客户只开放新增
（EAI 新增是 INSERT，不经清银行账号的 diffedit），不开放删除，新增错的客户用 customer_fields 写停用日期；
customer_address_delete 与 customer_address_add 同理成对。
type=vouchers：op 与单据类型 voucher 只能是登记表 readonly_voucher_ops.VOUCHER_OPS 里的一对（head_fix 发货单、
so_contract / create / update / close 销售订单、verify 销售订单 / 发货单 / 销售发票（发票只是复核，不含应收审核）、
generate 发货单参照销售订单或销售发票参照发货单），一项一对；
create、update、close、verify、generate 必须写 voucher，head_fix、so_contract 的单据类型固定，voucher 可省（写了须一致）。
请求路径必须恰好是 vouchers/<op>，请求体 type 必须等于 voucher（区分大小写），按 action 映射的写入分类 op 须在该行里
（verify 含审核、弃审，action 只能是 verify / unverify；close 只有关闭），generate 的 source_type 须是该单据类型的来源
（发货单 sale_order、销售发票 dispatch，可省）；也不能写 archives。
type=archives 不能写 voucher。
账套必须是 U8CO_READONLY_ACCOUNTS 的子集。未知键、其它 type/op、不合规的操作员编码一律让进程起不来。
操作员比较只忽略 ASCII 字母大小写（非 ASCII 字符逐字比较，与桥一致），不去首尾空白。archives/update（含客户）、其它档案的 create / delete、客户的 delete、
没列出的单据类型、vouchers/delete、vouchers/lock、旧版审核路由等其余写路由在只读账套上仍一律 403。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass

from u8co_api.readonly_voucher_ops import VOUCHER_OPS, voucher_covers, voucher_of

ENV = "U8CO_READONLY_EXCEPTIONS"
_KEYS = frozenset(("accounts", "operators", "callers", "type", "op", "archives", "voucher"))
_REQUIRED = ("accounts", "type", "op")
# 恰好写其一：按操作员放行，或按调用系统（信任项 name）放行。
_WHO_KEYS = ("operators", "callers")
OPERATOR = "operator"
CALLER = "caller"
_ARCHIVES = "archives"
_VOUCHERS = "vouchers"
_CREATE = "create"
_DELETE = "delete"
# 必须带 archives 的 op：新增与删除成对开放。
_ARCHIVE_OPS = (_CREATE, _DELETE)
_OPS = {
    _ARCHIVES: (
        "customer_person",
        "customer_fields",
        "customer_address",
        "customer_address_add",
        "customer_address_delete",
        *_ARCHIVE_OPS,
    ),
    _VOUCHERS: tuple(VOUCHER_OPS),
}
# type → 请求动作前缀：路径必须恰好是 <前缀><op>。
_PREFIX = {_ARCHIVES: "co:archives/", _VOUCHERS: "co:vouchers/"}
# op 为 create、delete 时 archives 可以列出的档案种类（代码里的封闭集合，配置只能从中选）。与桥
# ConfigRulesReadOnly.EligibleArchives / EligibleDelete 一致。客户不能删、update 不能开放。
CREATE_ARCHIVES = frozenset(("district_class", "customer"))
DELETE_ARCHIVES = frozenset(("district_class",))
_ELIGIBLE = {_CREATE: CREATE_ARCHIVES, _DELETE: DELETE_ARCHIVES}
# callers 只能用于这些 op（登记表里单据类型不固定的行：create、update、close、verify、generate），与桥
# ConfigRulesReadOnly.CallerOps 一致。head_fix、so_contract、archives 只能按操作员开放。
CALLER_OPS = frozenset(op for op, row in VOUCHER_OPS.items() if not row.fixed)
# 信任项 name 的写法（同 trust._NAME）。桥按 API 单独发来的 caller_trust 比较（co_idem.TRUST_FIELD），不再从 caller
# 里拆；sha256 仍不能写（以前 caller 摘要形如 sha256:<摘要>），保持 API 与桥的配置校验一致。
_TRUST_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,62}\Z")
_DIGEST_TAG = "sha256"
_SIZE = 65536
_ACCOUNT = re.compile(r"^[0-9]{3}\Z")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


@dataclass(frozen=True)
class ReadOnlyException:
    """一条只读例外。operators 保留配置原文，匹配时操作员只忽略 ASCII 大小写（ascii_fold）。
    callers 行的 operators 为空元组；operators 行的 callers 为空元组。"""

    accounts: tuple[str, ...]
    operators: tuple[str, ...]
    type: str
    op: str
    # 只有 op 为 create、delete 时有：允许写入的档案种类。
    archives: tuple[str, ...] | None = None
    # 只有 type=vouchers 有：配置的单据类型；head_fix、so_contract 省略时为 None（取固定的那一种）。
    voucher: str | None = None
    # 按调用系统放行：已验签的信任项 name（逐字比较）。只用于 type=vouchers 的 CALLER_OPS。
    callers: tuple[str, ...] = ()

    def matches(self, acc: str, operator: str) -> bool:
        """按操作员命中（operators 行）。"""
        if acc not in self.accounts or self.callers:
            return False
        who = ascii_fold(operator)
        return who in {ascii_fold(item) for item in self.operators}

    def matches_caller(self, acc: str, trust: str | None) -> bool:
        """按调用系统命中（callers 行）：信任项 name 逐字比较；没有已验签的信任项一律不命中。
        直接构造的对象若同时带 operators、或用于 CALLER_OPS 之外，也不命中。"""
        if not trust or not self.callers or self.operators or acc not in self.accounts:
            return False
        if self.type != _VOUCHERS or self.op not in CALLER_OPS:
            return False
        return trust in self.callers

    def match_kind(self, acc: str, operator: str, trust: str | None) -> str | None:
        """账套与身份命中的种类：operator、caller；不命中为 None（路由规则另看 covers）。"""
        if self.matches(acc, operator):
            return OPERATOR
        if self.matches_caller(acc, trust):
            return CALLER
        return None

    def covers(self, payload: dict, action: str | None = None) -> bool:
        """op 须是该 type 可开放的 op（直接构造的 op=delete 等一律不放行）；type=vouchers 按登记表看路径、请求体 type、
        写入分类 op 和生单来源（action 省略时按 vouchers/<op>）；archives 的 create、delete 另看请求体 archive。"""
        if self.op not in _OPS.get(self.type, ()):
            return False
        if self.type == _VOUCHERS:
            path = action or _PREFIX[_VOUCHERS] + self.op
            return self.archives is None and voucher_covers(self.op, self.voucher, path, payload)
        if self.op not in _ARCHIVE_OPS:
            return True
        # 解析器保证这两个 op 一定有 archives 且都可开放；对象被直接构造成没有、或列了不可开放的种类时，一律不放行。
        archive = _text(payload.get("archive"))
        return bool(self.archives) and archive in self.archives and archive in _ELIGIBLE[self.op]

    def summary(self) -> dict:
        found: dict = {"accounts": list(self.accounts)}
        if self.callers:
            found["callers"] = list(self.callers)
        else:
            found["operators"] = list(self.operators)
        found["type"] = self.type
        found["op"] = self.op
        if self.archives is not None:
            found["archives"] = list(self.archives)
        if self.voucher is not None:
            found["voucher"] = self.voucher
        return found


def parse_exceptions(raw: str, read_only: tuple[str, ...]) -> tuple[ReadOnlyException, ...]:
    """解析环境变量。空串为未配置；无效内容让进程起不来。"""
    if not raw:
        return ()
    data = _json(raw)
    if not isinstance(data, list):
        raise SystemExit(f"{ENV} 必须是 JSON 数组")
    return tuple(_one(f"{ENV}[{index}]", item, read_only) for index, item in enumerate(data))


def allows(exceptions: tuple[ReadOnlyException, ...], action: str, payload: dict, trust: str | None = None) -> bool:
    """路由必须恰好是 archives/<op> 或 vouchers/<op>，且任一同 type、op 例外的账套与操作员（或调用系统）命中才放行。
    archives 的 create、delete 另看请求体 archive；vouchers 另看请求体 type、action 与生单来源（readonly_voucher_ops）。
    trust 是已验签令牌的信任项 name（request.state.trust_name），不取自请求体。"""
    return match_of(exceptions, action, payload, trust) is not None


def match_of(
    exceptions: tuple[ReadOnlyException, ...], action: str, payload: dict, trust: str | None = None
) -> str | None:
    """命中的种类：operator（按操作员的行）优先于 caller（按调用系统的行）；都不命中为 None。与桥 ReadOnlyGate 一致。"""
    if not exceptions:
        return None
    acc = _text(payload.get("acc"))
    operator = _text(payload.get("operator"))
    found: str | None = None
    for item in exceptions:
        if action != _PREFIX.get(item.type, "") + item.op:
            continue
        kind = item.match_kind(acc, operator, trust)
        if kind is None or not item.covers(payload, action):
            continue
        if kind == OPERATOR:
            return OPERATOR
        found = CALLER
    return found


def health_of(exceptions: tuple[ReadOnlyException, ...]) -> list[dict] | None:
    """健康检查用的非机密摘要；未配置时省略。"""
    if not exceptions:
        return None
    return [item.summary() for item in exceptions]


def _json(raw: str) -> object:
    if len(raw) > _SIZE:
        raise SystemExit(f"{ENV} 超过 64 KiB")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{ENV} 不是合法的 JSON（第 {exc.lineno} 行）") from exc


def _one(where: str, raw: object, read_only: tuple[str, ...]) -> ReadOnlyException:
    if not isinstance(raw, dict):
        raise SystemExit(f"{where} 必须是对象")
    unknown = sorted(set(raw) - _KEYS)
    if unknown:
        raise SystemExit(f"{where} 有不认识的键: {', '.join(unknown)}")
    absent = [key for key in _REQUIRED if key not in raw]
    if absent:
        raise SystemExit(f"{where} 缺少: {', '.join(absent)}")
    who = [key for key in _WHO_KEYS if key in raw]
    if len(who) != 1:
        raise SystemExit(f"{where} 必须恰好写 operators 或 callers 之一（两个都写或都不写都不行）")
    type_, op = _kind(where, raw["type"], raw["op"])
    by_caller = who[0] == "callers"
    return ReadOnlyException(
        accounts=_accounts(where, raw["accounts"], read_only),
        operators=() if by_caller else _operators(where, raw["operators"]),
        type=type_,
        op=op,
        archives=_archives(where, type_, op, raw),
        voucher=_voucher(where, type_, op, raw),
        callers=_callers(where, type_, op, raw["callers"]) if by_caller else (),
    )


def _callers(where: str, type_: str, op: str, raw: object) -> tuple[str, ...]:
    # 只有 type=vouchers 的 CALLER_OPS 能按调用系统开放；值是信任项 name，逐字比较、不能重复、不能是 sha256。
    if type_ != _VOUCHERS or op not in CALLER_OPS:
        allowed = "、".join(op_ for op_ in VOUCHER_OPS if op_ in CALLER_OPS)
        raise SystemExit(f"{where}.callers 只能用于 type=vouchers 的 op={allowed}；其它项写 operators")
    found = _strings(where, raw, "callers", False)
    for name in found:
        if _TRUST_NAME.fullmatch(name) is None or name == _DIGEST_TAG:
            raise SystemExit(
                f"{where}.callers 里的项必须是信任项的 name（字母开头，字母、数字、_、-，不能是 sha256）: {name!r}"
            )
    return found


def _kind(where: str, type_: object, op: object) -> tuple[str, str]:
    if not isinstance(type_, str) or type_ not in _OPS:
        raise SystemExit(f"{where}.type 只能是 {'、'.join(_OPS)}")
    if op not in _OPS[type_]:
        raise SystemExit(f"{where}.op 在 type={type_} 时只能是 {'、'.join(_OPS[type_])}")
    return type_, str(op)


def _archives(where: str, type_: str, op: str, item: dict) -> tuple[str, ...] | None:
    # type=archives 且 op 为 create、delete 时必须列出档案种类，且都在代码里的可开放集合内；
    # 其它 type、op 不能写这个键（null 也不行）。
    if type_ != _ARCHIVES or op not in _ARCHIVE_OPS:
        if "archives" in item:
            raise SystemExit(f"{where}.archives 只能用于 type=archives 的 op=create 或 delete")
        return None
    if "archives" not in item:
        raise SystemExit(f"{where}.archives 在 op={op} 时必须给出")
    found = _strings(where, item["archives"], "archives", False)
    eligible = _ELIGIBLE[op]
    for kind in found:
        if kind not in eligible:
            allowed = "、".join(sorted(eligible))
            raise SystemExit(f"{where}.archives 里的 {kind!r} 不能开放 op={op}（目前只有 {allowed}）")
    return found


def _voucher(where: str, type_: str, op: str, item: dict) -> str | None:
    # type=vouchers：单据类型须在登记表该 op 的那一行里；单据类型固定的 op 可省。其它 type 不能写这个键（null 也不行）。
    if type_ != _VOUCHERS:
        if "voucher" in item:
            raise SystemExit(f"{where}.voucher 只能用于 type=vouchers")
        return None
    row = VOUCHER_OPS[op]
    if "voucher" not in item:
        if row.fixed:
            return None
        raise SystemExit(f"{where}.voucher 在 op={op} 时必须给出")
    voucher = item["voucher"]
    if not isinstance(voucher, str) or voucher_of(op, voucher) is None:
        raise SystemExit(f"{where}.voucher 在 op={op} 时只能是 {'、'.join(row.kinds)}")
    return voucher


def _accounts(where: str, raw: object, read_only: tuple[str, ...]) -> tuple[str, ...]:
    found = _strings(where, raw, "accounts", False)
    for acc in found:
        if _ACCOUNT.fullmatch(acc) is None:
            raise SystemExit(f"{where}.accounts 里的账套号必须是三位数字: {acc!r}")
        if acc not in read_only:
            raise SystemExit(f"{where}.accounts 里的账套 {acc} 不在 U8CO_READONLY_ACCOUNTS 里")
    return found


def _operators(where: str, raw: object) -> tuple[str, ...]:
    found = _strings(where, raw, "operators", True)
    for who in found:
        if not _operator_code(who):
            raise SystemExit(f"{where}.operators 里的项必须是有效的 U8 操作员编码: {who}")
    return found


def _strings(where: str, raw: object, key: str, ignore_case: bool) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise SystemExit(f"{where}.{key} 必须是非空数组")
    found: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item:
            raise SystemExit(f"{where}.{key} 里的项必须是非空字符串")
        token = ascii_fold(item) if ignore_case else item
        if token in seen:
            raise SystemExit(f"{where}.{key} 有重复的项: {item}")
        seen.add(token)
        found.append(item)
    return tuple(found)


def _operator_code(code: str) -> bool:
    # 与桥 ConfigRules.OperatorCode + JsonSafe 相同：1 到 20 个字符，不含空白、控制字符、单引号、分号、双引号、反斜杠。
    if not code or len(code) > 20:
        return False
    return all(ch > " " and ch not in "';\"\\" and unicodedata.category(ch) != "Cc" for ch in code)


def ascii_fold(code: str) -> str:
    """操作员比较的唯一规则（与桥 ReadOnlyGate.AsciiIgnoreCase 一致）：只把 A–Z 折成 a–z，其余字符（含非 ASCII）逐字比较。
    不用 casefold：它把 ß 折成 ss 等，桥做不到，两侧会不一致。"""
    return code.translate(_ASCII_LOWER)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""
