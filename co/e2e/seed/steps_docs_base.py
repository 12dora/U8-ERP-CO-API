"""造单据的公共部分：标记、按标记或来源查已有单据、桥调用、审核、金额计算，以及单据步骤的外壳。

幂等：自己新建的单据在表头备注（cmemo、cDigest、remark 等）写固定标记 seed:<步骤名>，重跑前先用 SQL 按标记找；
不能带表头字段的生单（销售出库、报检单、检验单、采购发票）按来源单据找；材料出库、产成品入库生单带 cmemo，按标记找。找到的主键记进 ctx.state["docs"]。
标识符都是本模块的常量；拼进 SQL 的只有正整数主键和经 quoted() 检查过的编码。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from co.e2e.seed.base import Probe, Step, fail_text, quoted
from co.e2e.seed.context import SeedContext, SeedError

MARK = "seed:"
CREATE = "v1/vouchers/create"
GENERATE = "v1/vouchers/generate"
VERIFY = "v1/vouchers/verify"
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_INT_MAX = 2147483647
_CENT = Decimal("0.01")

# 按表头备注找单据：类型 → (表, 主键列, 备注列)。
MEMO_TABLE = {
    "sale_order": ("SO_SOMain", "ID", "cMemo"),
    "dispatch": ("DispatchList", "DLID", "cMemo"),
    "sale_invoice": ("SaleBillVouch", "SBVID", "cMemo"),
    "purchase_order": ("PO_Pomain", "POID", "cMemo"),
    "arrival": ("PU_ArrivalVouch", "ID", "cMemo"),
    "purchase_in": ("RdRecord01", "ID", "cMemo"),
    "other_in": ("RdRecord08", "ID", "cMemo"),
    "other_out": ("RdRecord09", "ID", "cMemo"),
    "material_out": ("RdRecord11", "ID", "cMemo"),
    "product_in": ("RdRecord10", "ID", "cMemo"),
    "transfer": ("TransVouch", "ID", "cTVMemo"),
}

# 已审核的判定：类型 → 按主键数「已审核」行数的 SQL 模板（{id} 是正整数主键）。
VERIFIED_SQL = {
    "sale_order": "SELECT COUNT(*) AS n FROM SO_SOMain WHERE ID = {id} AND ISNULL(cVerifier, N'') <> N''",
    "dispatch": "SELECT COUNT(*) AS n FROM DispatchList WHERE DLID = {id} AND ISNULL(cVerifier, N'') <> N''",
    "purchase_order": "SELECT COUNT(*) AS n FROM PO_Pomain WHERE POID = {id} AND ISNULL(cVerifier, N'') <> N''",
    "arrival": "SELECT COUNT(*) AS n FROM PU_ArrivalVouch WHERE ID = {id} AND ISNULL(cverifier, N'') <> N''",
    "purchase_in": "SELECT COUNT(*) AS n FROM RdRecord01 WHERE ID = {id} AND ISNULL(cHandler, N'') <> N''",
    "other_in": "SELECT COUNT(*) AS n FROM RdRecord08 WHERE ID = {id} AND ISNULL(cHandler, N'') <> N''",
    "other_out": "SELECT COUNT(*) AS n FROM RdRecord09 WHERE ID = {id} AND ISNULL(cHandler, N'') <> N''",
    "bom": "SELECT COUNT(*) AS n FROM bom_bom WHERE BomId = {id} AND Status = 3",
    # 生产订单：全部行都已审核才算。
    "production_order": (
        "SELECT CASE WHEN COUNT(*) > 0 AND SUM(CASE WHEN Status = 3 THEN 1 ELSE 0 END) = COUNT(*)"
        " THEN 1 ELSE 0 END AS n FROM mom_orderdetail WHERE MoId = {id}"
    ),
    "ar_receipt": (
        "SELECT COUNT(*) AS n FROM Ap_CloseBill WHERE iID = {id} AND cFlag = N'AR' AND ISNULL(cCheckMan, N'') <> N''"
    ),
    "ap_payment": (
        "SELECT COUNT(*) AS n FROM Ap_CloseBill WHERE iID = {id} AND cFlag = N'AP' AND ISNULL(cCheckMan, N'') <> N''"
    ),
}


def marker(key: str) -> str:
    return MARK + key


def safe_id(value: object) -> int:
    text = str(value).strip()
    if _ID.fullmatch(text) is None or int(text) > _INT_MAX:
        raise SeedError("主键不合法：" + text[:30])
    return int(text)


def plus_days(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def ids_of(rows: list[dict[str, str]], column: str = "id") -> list[int]:
    return [safe_id(row.get(column, "")) for row in rows if row.get(column, "").strip()]


def memo_sql(kind: str, key: str) -> str:
    table, pk, col = MEMO_TABLE[kind]
    return f"SELECT {pk} AS id FROM {table} WHERE {col} = {quoted([marker(key)])} ORDER BY {pk}"


def find_marked(ctx: SeedContext, kind: str, key: str) -> list[int]:
    return ids_of(ctx.rows(memo_sql(kind, key)))


def find_ids(ctx: SeedContext, sql: str) -> list[int]:
    return ids_of(ctx.rows(sql))


def line_ids(ctx: SeedContext, sql: str, doc_id: int) -> list[int]:
    """来源单据的表体行主键（sql 里 {id} 换成来源主键），按主键排序；一行都没有时失败。"""
    found = ids_of(ctx.rows(sql.format(id=safe_id(doc_id))))
    if not found:
        raise SeedError("来源单据 " + str(doc_id) + " 没有表体行", saved=ctx.last_call)
    return found


def response_id(ctx: SeedContext, what: str, response: dict) -> int:
    if response.get("ok") is not True or _ID.fullmatch(str(response.get("id", ""))) is None:
        raise SeedError(what + " 失败：" + fail_text(response), response, ctx.last_call)
    return int(response["id"])


def post_ok(ctx: SeedContext, path: str, body: dict, what: str) -> dict:
    response = ctx.call(path, body)
    if response.get("ok") is not True:
        raise SeedError(what + " 失败：" + fail_text(response), response, ctx.last_call)
    return response


def create_doc(ctx: SeedContext, body: dict) -> int:
    return response_id(ctx, "新增 " + str(body.get("type")), ctx.call(CREATE, body))


def generate_doc(ctx: SeedContext, body: dict) -> int:
    return response_id(ctx, "生单 " + str(body.get("type")), ctx.call(GENERATE, body))


def is_verified(ctx: SeedContext, kind: str, doc_id: int) -> bool:
    rows = ctx.rows(VERIFIED_SQL[kind].format(id=safe_id(doc_id)))
    return bool(rows) and rows[0].get("n", "0").strip() not in ("", "0")


def ensure_verified(ctx: SeedContext, kind: str, doc_id: int) -> None:
    # 先按 SQL 看是否已审核，重跑时不再发审核（已审核再审是 409）。
    if is_verified(ctx, kind, doc_id):
        return
    post_ok(ctx, VERIFY, {"type": kind, "id": doc_id, "action": "verify"}, "审核 " + kind + " " + str(doc_id))


def remember_doc(ctx: SeedContext, key: str, ids: list[int]) -> None:
    ctx.state.setdefault("docs", {})[key] = list(ids)


def need_one(ctx: SeedContext, find: Callable[[SeedContext], list[int]], step: str) -> int:
    found = find(ctx)
    if not found:
        raise SeedError("前面的步骤 " + step + " 还没有造出单据，先跑它")
    return found[0]


def one_or_none(ctx: SeedContext, find: Callable[[SeedContext], list[int]]) -> int:
    found = find(ctx)
    return found[0] if found else 0


# ---- 金额 ----


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def taxed_amounts(qty: int | str, taxed_price: int | str, rate: int = 13) -> dict:
    """按数量 × 含税单价和税率算销售订单行的金额字段（原币、本币相同，人民币汇率 1），2 位小数。"""
    q, p = Decimal(str(qty)), Decimal(str(taxed_price))
    factor = 1 + Decimal(rate) / 100
    total = _money(q * p)
    money = _money(total / factor)
    tax = total - money
    net = _money(p / factor)
    values = {
        "iquotedprice": p,
        "itaxunitprice": p,
        "iunitprice": net,
        "imoney": money,
        "itax": tax,
        "isum": total,
        "inatunitprice": net,
        "inatmoney": money,
        "inattax": tax,
        "inatsum": total,
    }
    out: dict = {key: float(value) for key, value in values.items()}
    out["itaxrate"] = rate
    return out


# ---- 单据步骤外壳 ----


@dataclass(frozen=True)
class Doc:
    key: str
    title: str
    # 已有的单据主键（按标记或来源找）；上游还没有时返回空列表，不报错（plan 要用）。
    find: Callable[[SeedContext], list[int]]
    # 新建一张，返回主键。
    make: Callable[[SeedContext], int]
    # 每张单据的后续动作（审核、制单等），必须幂等。
    after: Callable[[SeedContext, int], None] | None = None
    count: int = 1


def _probe(doc: Doc) -> Callable[[SeedContext], Probe]:
    def probe(ctx: SeedContext) -> Probe:
        found = doc.find(ctx)[: doc.count]
        existing = tuple(doc.key + ":" + str(item) for item in found)
        missing = tuple(doc.key + "#" + str(n + 1) for n in range(len(found), doc.count))
        return Probe(existing, missing)

    return probe


def _apply(doc: Doc) -> Callable[[SeedContext], tuple[int, int]]:
    def apply(ctx: SeedContext) -> tuple[int, int]:
        found = doc.find(ctx)[: doc.count]
        existed = len(found)
        while len(found) < doc.count:
            found.append(doc.make(ctx))
            remember_doc(ctx, doc.key, found)
            ctx.save_state()
        for doc_id in found:
            if doc.after is not None:
                doc.after(ctx, doc_id)
        remember_doc(ctx, doc.key, found)
        return len(found) - existed, existed

    return apply


def doc_step(doc: Doc) -> Step:
    return Step(doc.key, doc.title, _probe(doc), _apply(doc))


def verify_after(kind: str) -> Callable[[SeedContext, int], None]:
    return lambda ctx, doc_id: ensure_verified(ctx, kind, doc_id)
