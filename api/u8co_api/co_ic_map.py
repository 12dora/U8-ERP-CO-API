"""公司间对照（U8CO_IC_MAP_FILE）：哪些账套是同一公司组、彼此在对方账套里的客户 / 供应商编码、
存货对照、总账科目对照和抵销对。文件有任何问题进程都起不来（同 U8CO_BRIDGE_ROUTES_FILE）。

真实的编码、税号和公司名称是现场配置，不进代码库；示例和测试只用占位值。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any

from u8co_api.errors import ApiError, bad_request

_LIMIT = 262144
_ACCOUNT = re.compile(r"^[0-9]{3}\Z")
_GROUP_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}\Z")
_LOGICAL = re.compile(r"^[a-z0-9_]{1,40}\Z")
_CODE = re.compile(r"^[^\x00-\x1f\x7f]{1,60}\Z")
_GL_CODE = re.compile(r"^[0-9]{1,20}\Z")
_GROUP_REQUIRED = frozenset(("id", "accounts"))
_GROUP_KEYS = _GROUP_REQUIRED | {"names", "as_customer", "as_vendor", "inventory", "gl", "elim"}
_INV_KEYS = frozenset(("id", "codes", "match"))
_GL_KEYS = frozenset(("logical", "codes"))
_ELIM_KEYS = frozenset(("rule", "pairs"))
_REV_COGS_KEYS = frozenset(("rule", "seller", "buyer", "revenue_source", "cost_source"))
_PAIR_KEYS = frozenset(("ar", "ap"))
MATCH_CODE = "code"
MATCH_QTY_DATE = "qty_date"
_MATCH_MODES = (MATCH_CODE, MATCH_QTY_DATE)
ELIM_RULES = ("ar_ap", "rev_cogs")
# 经营管理合并利润表的内部收入、成本抵销：卖方账套里归属买方公司的收入、成本从哪里取。
# sales_to_customer / ia_to_customer：卖方销售统计里买方公司这个客户（as_customer）的收入 / 销售成本；
# gl_revenue_all / gl_cogs_all：卖方利润表的整行营业收入 / 营业成本（卖方只卖给买方时用）。
REVENUE_SOURCES = ("sales_to_customer", "gl_revenue_all")
COST_SOURCES = ("ia_to_customer", "gl_cogs_all")


def _fail(message: str) -> SystemExit:
    return SystemExit(f"U8CO_IC_MAP_FILE: {message}")


@dataclass(frozen=True, eq=False)
class IcInventory:
    """一组账套里是同一个存货的编码。match 为 code 时按存货 + 数量 + 日期（可放宽天数）配对，qty_date 时只按数量 + 当天配对。"""

    id: str
    codes: dict[str, str]
    match: str = MATCH_CODE


@dataclass(frozen=True, eq=False)
class IcGl:
    """一个逻辑科目在各账套里的科目编码。"""

    logical: str
    codes: dict[str, str]


@dataclass(frozen=True, eq=False)
class IcSide:
    """抵销对的一方：账套、逻辑科目、往来单位编码（该账套里的客户或供应商编码）。"""

    acc: str
    logical: str
    partner: str


@dataclass(frozen=True, eq=False)
class IcElimPair:
    ar: IcSide
    ap: IcSide


@dataclass(frozen=True, eq=False)
class IcElim:
    """rule 为 ar_ap 时用 pairs；为 rev_cogs 时用 seller、buyer、revenue_source、cost_source（pairs 为空）。"""

    rule: str
    pairs: tuple[IcElimPair, ...] = ()
    seller: str = ""
    buyer: str = ""
    revenue_source: str = ""
    cost_source: str = ""


@dataclass(frozen=True, eq=False)
class IcGroup:
    id: str
    accounts: tuple[str, ...]
    names: dict[str, str]
    # as_customer[x][y]：账套 x 里代表公司 y 的客户编码；as_vendor 同理是供应商编码。
    as_customer: dict[str, dict[str, str]]
    as_vendor: dict[str, dict[str, str]]
    inventory: tuple[IcInventory, ...] = ()
    gl: tuple[IcGl, ...] = ()
    elim: tuple[IcElim, ...] = ()

    def name(self, acc: str) -> str:
        return self.names.get(acc, acc)

    def customer_code(self, in_acc: str, company_acc: str) -> str | None:
        return self.as_customer.get(in_acc, {}).get(company_acc)

    def vendor_code(self, in_acc: str, company_acc: str) -> str | None:
        return self.as_vendor.get(in_acc, {}).get(company_acc)

    def company_of_customer(self, in_acc: str, code: str) -> str | None:
        return _owner(self.as_customer.get(in_acc, {}), code)

    def company_of_vendor(self, in_acc: str, code: str) -> str | None:
        return _owner(self.as_vendor.get(in_acc, {}), code)

    def inventory_of(self, acc: str, code: str) -> IcInventory | None:
        for item in self.inventory:
            if item.codes.get(acc) == code:
                return item
        return None

    def inventory_codes(self, acc: str) -> list[str]:
        return [item.codes[acc] for item in self.inventory if acc in item.codes]

    def gl_of(self, logical: str) -> IcGl | None:
        for item in self.gl:
            if item.logical == logical:
                return item
        return None

    def gl_logical(self, acc: str, code: str) -> str | None:
        for item in self.gl:
            if item.codes.get(acc) == code:
                return item.logical
        return None


def _owner(codes: dict[str, str], code: str) -> str | None:
    for company, found in codes.items():
        if found == code:
            return company
    return None


@dataclass(frozen=True, eq=False)
class IcMap:
    groups: tuple[IcGroup, ...]

    def group_of(self, accs: set[str] | frozenset[str]) -> IcGroup:
        """这些账套都在同一个公司组里时返回该组，否则 400 ic_group_mismatch。"""
        for group in self.groups:
            if accs and set(accs) <= set(group.accounts):
                return group
        raise group_mismatch()


def group_mismatch() -> ApiError:
    error = bad_request("账套不在同一公司组", "ic_group_mismatch")
    error.hint = "核对 logins 里的账套是否属于公司间对照里的同一组"
    return error


def load_ic_map(path: str) -> IcMap:
    """读公司间对照文件并严格校验，任何问题都让进程起不来。"""
    return parse_ic_map(_json(path))


def parse_ic_map(data: object) -> IcMap:
    """校验已解析的 JSON（load_ic_map 和测试共用）。"""
    if not isinstance(data, dict) or set(data) != {"groups"}:
        raise _fail('顶层必须是只含 "groups" 列表的对象')
    raw = data["groups"]
    if not isinstance(raw, list) or not raw:
        raise _fail("groups 必须是非空列表")
    groups = tuple(_group(f"groups[{index}]", item) for index, item in enumerate(raw))
    _distinct_groups(groups)
    return IcMap(groups)


def _json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read(_LIMIT + 1)
    except (OSError, UnicodeDecodeError) as exc:
        raise _fail("文件读不到") from exc
    if len(text) > _LIMIT:
        raise _fail("文件超过 256 KiB")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise _fail(f"不是合法的 JSON（第 {exc.lineno} 行）") from exc


def _distinct_groups(groups: tuple[IcGroup, ...]) -> None:
    ids: set[str] = set()
    accs: set[str] = set()
    for group in groups:
        if group.id in ids:
            raise _fail(f"组 id {group.id} 重复")
        ids.add(group.id)
        again = sorted(accs.intersection(group.accounts))
        if again:
            raise _fail(f"账套 {again[0]} 出现在多个组里")
        accs.update(group.accounts)


def _keys(where: str, raw: object, required: frozenset[str], allowed: frozenset[str]) -> dict:
    if not isinstance(raw, dict):
        raise _fail(f"{where} 必须是对象")
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise _fail(f"{where} 有不认识的键: {', '.join(unknown)}")
    absent = sorted(required - set(raw))
    if absent:
        raise _fail(f"{where} 缺少: {', '.join(absent)}")
    return raw


def _group(where: str, raw: object) -> IcGroup:
    data = _keys(where, raw, _GROUP_REQUIRED, _GROUP_KEYS)
    group_id = data["id"]
    if not isinstance(group_id, str) or _GROUP_ID.fullmatch(group_id) is None:
        raise _fail(f"{where}.id 必须是 1 到 40 个字母、数字、下划线或短横")
    accounts = _accounts(f"{where}.accounts", data["accounts"])
    customers = _parties(f"{where}.as_customer", data.get("as_customer", {}), accounts)
    vendors = _parties(f"{where}.as_vendor", data.get("as_vendor", {}), accounts)
    group = IcGroup(
        id=group_id,
        accounts=accounts,
        names=_names(f"{where}.names", data.get("names", {}), accounts),
        as_customer=customers,
        as_vendor=vendors,
        inventory=_inventories(f"{where}.inventory", data.get("inventory", []), accounts),
        gl=_gls(f"{where}.gl", data.get("gl", []), accounts),
    )
    elim = _elims(f"{where}.elim", data.get("elim", []), group)
    return replace(group, elim=elim)


def _accounts(where: str, raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list) or len(raw) < 2:
        raise _fail(f"{where} 必须是至少 2 个账套号的列表")
    for item in raw:
        if not isinstance(item, str) or _ACCOUNT.fullmatch(item) is None:
            raise _fail(f"{where} 里的账套号必须是三位数字的字符串: {item!r}")
    if len(set(raw)) != len(raw):
        raise _fail(f"{where} 有重复的账套号")
    return tuple(raw)


def _acc_in(where: str, acc: object, accounts: tuple[str, ...]) -> str:
    if not isinstance(acc, str) or acc not in accounts:
        raise _fail(f"{where} 的账套 {acc!r} 不在本组 accounts 里")
    return acc


def _code(where: str, raw: object, pattern: re.Pattern[str] = _CODE) -> str:
    if not isinstance(raw, str) or pattern.fullmatch(raw) is None:
        raise _fail(f"{where} 必须是非空、不含控制字符的编码")
    return raw


def _names(where: str, raw: object, accounts: tuple[str, ...]) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise _fail(f"{where} 必须是对象")
    return {_acc_in(where, acc, accounts): _code(f"{where}.{acc}", name) for acc, name in raw.items()}


def _parties(where: str, raw: object, accounts: tuple[str, ...]) -> dict[str, dict[str, str]]:
    if not isinstance(raw, dict):
        raise _fail(f"{where} 必须是对象")
    found: dict[str, dict[str, str]] = {}
    for acc, inner in raw.items():
        _acc_in(where, acc, accounts)
        if not isinstance(inner, dict):
            raise _fail(f"{where}.{acc} 必须是对象")
        codes: dict[str, str] = {}
        for company, code in inner.items():
            _acc_in(f"{where}.{acc}", company, accounts)
            if company == acc:
                raise _fail(f"{where}.{acc} 不能对照本账套自己")
            codes[company] = _code(f"{where}.{acc}.{company}", code)
        if len(set(codes.values())) != len(codes):
            raise _fail(f"{where}.{acc} 有两家公司用了同一个编码")
        found[acc] = codes
    return found


def _codes(where: str, raw: object, accounts: tuple[str, ...], pattern: re.Pattern[str]) -> dict[str, str]:
    if not isinstance(raw, dict) or not raw:
        raise _fail(f"{where} 必须是非空对象（账套号 → 编码）")
    return {_acc_in(where, acc, accounts): _code(f"{where}.{acc}", code, pattern) for acc, code in raw.items()}


def _unique_per_acc(where: str, rows: list[dict[str, str]]) -> None:
    seen: set[tuple[str, str]] = set()
    for codes in rows:
        for pair in codes.items():
            if pair in seen:
                raise _fail(f"{where} 里账套 {pair[0]} 的编码 {pair[1]} 出现在多项里")
            seen.add(pair)


def _inventories(where: str, raw: object, accounts: tuple[str, ...]) -> tuple[IcInventory, ...]:
    if not isinstance(raw, list):
        raise _fail(f"{where} 必须是列表")
    found: list[IcInventory] = []
    for index, item in enumerate(raw):
        at = f"{where}[{index}]"
        data = _keys(at, item, frozenset(("id", "codes")), _INV_KEYS)
        item_id = _code(f"{at}.id", data["id"], _GROUP_ID)
        match = data.get("match", MATCH_CODE)
        if match not in _MATCH_MODES:
            raise _fail(f"{at}.match 只能是 code 或 qty_date")
        found.append(IcInventory(item_id, _codes(f"{at}.codes", data["codes"], accounts, _CODE), match))
    if len({item.id for item in found}) != len(found):
        raise _fail(f"{where} 的 id 有重复")
    _unique_per_acc(where, [item.codes for item in found])
    return tuple(found)


def _gls(where: str, raw: object, accounts: tuple[str, ...]) -> tuple[IcGl, ...]:
    if not isinstance(raw, list):
        raise _fail(f"{where} 必须是列表")
    found: list[IcGl] = []
    for index, item in enumerate(raw):
        at = f"{where}[{index}]"
        data = _keys(at, item, _GL_KEYS, _GL_KEYS)
        logical = _code(f"{at}.logical", data["logical"], _LOGICAL)
        found.append(IcGl(logical, _codes(f"{at}.codes", data["codes"], accounts, _GL_CODE)))
    if len({item.logical for item in found}) != len(found):
        raise _fail(f"{where} 的 logical 有重复")
    _unique_per_acc(where, [item.codes for item in found])
    return tuple(found)


def _elims(where: str, raw: object, group: IcGroup) -> tuple[IcElim, ...]:
    if not isinstance(raw, list):
        raise _fail(f"{where} 必须是列表")
    found: list[IcElim] = []
    for index, item in enumerate(raw):
        at = f"{where}[{index}]"
        rule = item.get("rule") if isinstance(item, dict) else None
        if rule not in ELIM_RULES:
            raise _fail(f"{at}.rule 只能是 " + "、".join(ELIM_RULES))
        if rule == "rev_cogs":
            found.append(_rev_cogs(at, item, group, found))
            continue
        data = _keys(at, item, _ELIM_KEYS, _ELIM_KEYS)
        pairs = data["pairs"]
        if not isinstance(pairs, list) or not pairs:
            raise _fail(f"{at}.pairs 必须是非空列表")
        found.append(IcElim(data["rule"], tuple(_pair(f"{at}.pairs[{n}]", p, group) for n, p in enumerate(pairs))))
    return tuple(found)


def _rev_cogs(where: str, raw: object, group: IcGroup, earlier: list[IcElim]) -> IcElim:
    data = _keys(where, raw, _REV_COGS_KEYS, _REV_COGS_KEYS)
    seller = _acc_in(f"{where}.seller", data["seller"], group.accounts)
    buyer = _acc_in(f"{where}.buyer", data["buyer"], group.accounts)
    if seller == buyer:
        raise _fail(f"{where} 的 seller 和 buyer 不能是同一个账套")
    if data["revenue_source"] not in REVENUE_SOURCES:
        raise _fail(f"{where}.revenue_source 只能是 " + "、".join(REVENUE_SOURCES))
    if data["cost_source"] not in COST_SOURCES:
        raise _fail(f"{where}.cost_source 只能是 " + "、".join(COST_SOURCES))
    by_customer = data["revenue_source"] == "sales_to_customer" or data["cost_source"] == "ia_to_customer"
    if by_customer and group.customer_code(seller, buyer) is None:
        raise _fail(f"{where} 按客户取数，但 as_customer.{seller} 里没有代表账套 {buyer} 的客户编码")
    if any(item.rule == "rev_cogs" and (item.seller, item.buyer) == (seller, buyer) for item in earlier):
        raise _fail(f"{where} 的 rev_cogs {seller}→{buyer} 重复")
    return IcElim("rev_cogs", (), seller, buyer, data["revenue_source"], data["cost_source"])


def _pair(where: str, raw: object, group: IcGroup) -> IcElimPair:
    data = _keys(where, raw, _PAIR_KEYS, _PAIR_KEYS)
    ar = _elim_side(f"{where}.ar", data["ar"], group)
    ap = _elim_side(f"{where}.ap", data["ap"], group)
    # 应收方的客户必须就是应付方公司，应付方的供应商必须就是应收方公司。
    if group.company_of_customer(ar.acc, ar.partner) != ap.acc:
        raise _fail(f"{where}.ar 的客户 {ar.partner} 在 as_customer.{ar.acc} 里不是账套 {ap.acc}")
    if group.company_of_vendor(ap.acc, ap.partner) != ar.acc:
        raise _fail(f"{where}.ap 的供应商 {ap.partner} 在 as_vendor.{ap.acc} 里不是账套 {ar.acc}")
    return IcElimPair(ar, ap)


def _elim_side(where: str, raw: object, group: IcGroup) -> IcSide:
    if not isinstance(raw, list) or len(raw) != 3:
        raise _fail(f"{where} 必须是 [账套, 逻辑科目, 往来单位编码]")
    acc = _acc_in(where, raw[0], group.accounts)
    logical = _code(f"{where}[1]", raw[1], _LOGICAL)
    gl = group.gl_of(logical)
    if gl is None or acc not in gl.codes:
        raise _fail(f"{where} 的逻辑科目 {logical} 在 gl 里没有账套 {acc} 的科目")
    return IcSide(acc, logical, _code(f"{where}[2]", raw[2]))
