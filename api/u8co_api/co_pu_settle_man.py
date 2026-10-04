"""采购手工结算（vouchers/create，type=purchase_settle）的请求校验，与桥 PuSettleManReq 一致。

是第一级写入（桥 WriteClassSelfTest.CheckPromoted），不只限测试账套，按写入策略放行；
桥按 U8 手工结算界面执行的 SQL 写（实测核对）。
表头只收 settle_date（yyyy-MM-dd，可省，给了就作为这一笔的 U8 登录日期）；明细 1 到 400 行，每行
in_line_id（采购入库行 AutoID，0 或省略表示没有）、invoice_line_id（采购发票行 ID，同上）、quantity（带符号、不为 0）、
amount（可省，结算金额；红蓝入库对冲行不收）。其他明细类型仍是 1 到 200 行。
"""

from __future__ import annotations

from u8co_api.co_doctext import section
from u8co_api.co_gen_pu_settle import SETTLE_KIND, check_settle_gen

LINES_MAX = 400
OTHER_LINES_MAX = 200
_ID_MAX = 2147483647
_NUM_MAX = 10**12
_KEYS = frozenset(("in_line_id", "invoice_line_id", "quantity", "amount"))
SETTLE_CREATE_HELP = section(
    "采购结算单手工结算（purchase_settle，第一级写入）",
    (
        "表头只收 settle_date（可省，即本次 U8 登录日期）",
        "明细 1 到 400 行，每行 in_line_id（采购入库行 AutoID）、invoice_line_id（采购发票行 ID）、quantity（带符号）",
        "amount 可省：结算无税金额，缺省按发票行金额比例",
        "两个 id 都给是配对；只给 in_line_id 是红蓝入库对冲；只给 invoice_line_id 是红蓝发票对冲",
        "对冲时各自同一存货的数量合计须为 0",
        "只做普通采购、人民币、专用或普通发票、一个供应商",
        "委外、外币、费用分摊、同一发票行既对冲又配对：400 / 409，请在 U8 客户端结算",
    ),
)


def _id(value: object, key: str) -> int:
    if type(value) is not int or not 0 <= value <= _ID_MAX:
        raise ValueError(key + " 必须是 0 到 2147483647 的整数")
    return value


def _num(value: object, key: str, zero_ok: bool) -> float:
    if type(value) not in (int, float) or value != value or abs(value) > _NUM_MAX or (value == 0 and not zero_ok):
        rule = "绝对值不超过 1000000000000 的数" if zero_ok else "不为 0、绝对值不超过 1000000000000 的数"
        raise ValueError(key + " 必须是" + rule)
    return float(value)


def _line(raw: dict) -> tuple[int, int]:
    unknown = [key for key in raw if key not in _KEYS]
    if unknown:
        raise ValueError("不能设置字段 " + str(unknown[0]))
    rd = _id(raw.get("in_line_id", 0), "in_line_id")
    bill = _id(raw.get("invoice_line_id", 0), "invoice_line_id")
    if rd == 0 and bill == 0:
        raise ValueError("in_line_id、invoice_line_id 至少给一个")
    if "quantity" not in raw:
        raise ValueError("缺少 quantity")
    qty = _num(raw["quantity"], "quantity", False)
    if "amount" in raw:
        if bill == 0:
            raise ValueError("红蓝入库对冲行（没有发票行）不能指定 amount，按暂估金额结算")
        amount = _num(raw["amount"], "amount", True)
        if amount != 0 and (amount > 0) != (qty > 0):
            raise ValueError("amount 的正负必须与 quantity 一致")
    return rd, bill


def check_settle_create(kind: str, head: dict | None, lines: list[dict] | None) -> None:
    """手工结算的表头与明细；其他类型只管明细不超过 200 行（采购结算单放宽到 400 行）。"""
    count = len(lines or [])
    if kind != SETTLE_KIND:
        if count > OTHER_LINES_MAX:
            raise ValueError("明细最多 200 行")
        return
    check_settle_gen(head, None)
    seen: set[tuple[int, int]] = set()
    for raw in lines or []:
        pair = _line(raw)
        if pair in seen:
            raise ValueError("入库行与发票行的组合重复")
        seen.add(pair)
