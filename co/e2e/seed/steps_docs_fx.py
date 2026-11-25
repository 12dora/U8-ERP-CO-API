"""造单据（八）：外币汇率和外币收付款单、应收应付单（给外币核销用例）。

- fx_rate：美元（data.CURRENCIES）造数月份的记账汇率（固定汇率，exch.itype=2）。已有就沿用已有的值，没有才经
  archives/create 新增 exchange_rate「美元:<年>:<月>」，汇率 FX_RATE。之后各单据都按 SQL 读出的这个汇率写。
- fx_ar_receipt：C0001 美元收款单一行（应收款，原币 FX_AMOUNT），审核，不制单。
- fx_ar_bill：C0001 美元应收单一行（原币 FX_AMOUNT），审核。
- fx_ap_payment / fx_ap_bill：S0001 的美元付款单、应付单，同上。
同一往来单位、同一币种、同一汇率，给 ar-writeoff-fx / ap-writeoff-fx。现有核销用例的挑单只取本位币、汇率 1 的单据，不受影响。
摘要 cDigest 写 seed:fx_*，按它找。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from co.e2e.seed import data
from co.e2e.seed.base import Probe, Step, quoted
from co.e2e.seed.context import SeedContext, SeedError
from co.e2e.seed.steps_docs_arap import (
    bill_body,
    bill_km,
    control_km,
    payment_body,
    receipt_body,
)
from co.e2e.seed.steps_docs_base import (
    Doc,
    create_doc,
    doc_step,
    find_ids,
    marker,
    post_ok,
    safe_id,
)

CURRENCY = data.CURRENCIES[0].code
FX_RATE = Decimal("7.1")
FX_AMOUNT = 20
_RATE_TYPE = 2
_ARCHIVE = "exchange_rate"

_RATE_SQL = (
    "SELECT TOP (1) CONVERT(varchar(40), CONVERT(decimal(18, 6), nflat)) AS rate FROM exch"
    " WHERE cexch_name = {name} AND iYear = {year} AND iperiod = {period} AND itype = " + str(_RATE_TYPE)
    + " ORDER BY i_id"
)
_USD_BANK_SQL = (
    "SELECT TOP (1) ccode AS code FROM code WHERE iyear = {year} AND bend = 1 AND ccode LIKE N'1002%'"
    " AND cexch_name = {name} ORDER BY ccode"
)
_CLOSE_FIND_SQL = (
    "SELECT iID AS id FROM Ap_CloseBill WHERE cFlag = N'{flag}' AND cVouchType = N'{vouch}'"
    " AND cDigest = {mark} ORDER BY iID"
)
_BILL_FIND_SQL = (
    "SELECT Auto_ID AS id FROM Ap_Vouch WHERE cFlag = N'{flag}' AND cVouchType = N'{vouch}'"
    " AND cDigest = {mark} ORDER BY Auto_ID"
)
# 审核看表头 cCheckMan（收付款单在 Ap_CloseBill，应收应付单在 Ap_Vouch）。
_CHECKED_SQL = {
    "close": "SELECT COUNT(*) AS n FROM Ap_CloseBill WHERE iID = {id} AND ISNULL(cCheckMan, N'') <> N''",
    "bill": "SELECT COUNT(*) AS n FROM Ap_Vouch WHERE Auto_ID = {id} AND ISNULL(cCheckMan, N'') <> N''",
}
# 步骤名 → (单据类型, cFlag, cVouchType, 是否收付款单, 受控科目取哪一侧)。
FX_DOCS = {
    "fx_ar_receipt": ("ar_receipt", "AR", "48", True, "ar_receipt"),
    "fx_ar_bill": ("ar_bill", "AR", "R0", False, "ar_receipt"),
    "fx_ap_payment": ("ap_payment", "AP", "49", True, "ap_payment"),
    "fx_ap_bill": ("ap_bill", "AP", "P0", False, "ap_payment"),
}


# ---- 纯函数 ----


def period_of(day: str) -> tuple[int, int]:
    try:
        parsed = date.fromisoformat(day)
    except ValueError as exc:
        raise SeedError("日期不合法：" + day) from exc
    return parsed.year, parsed.month


def rate_code(day: str) -> str:
    year, period = period_of(day)
    return f"{CURRENCY}:{year}:{period}"


def rate_sql(day: str) -> str:
    year, period = period_of(day)
    return _RATE_SQL.format(name=quoted([CURRENCY]), year=year, period=period)


def rate_body(day: str) -> dict:
    return {"archive": _ARCHIVE, "code": rate_code(day), "fields": {"rate": float(FX_RATE)}}


def _fx_head(head: dict, key: str, rate: Decimal) -> None:
    head.update({"cDigest": marker(key), "cexch_name": CURRENCY, "iExchRate": float(rate)})


def fx_body(key: str, day: str, kms: tuple[str, str], rate: Decimal) -> dict:
    """kms：（受控科目, 应收应付单表体科目，收付款单不用）。金额只给原币，本币由桥按汇率折算。"""
    kind, _flag, _vouch, close, _side = FX_DOCS[key]
    ctl, km = kms
    if not close:
        body = bill_body(kind, day, ctl, km)
        body["lines"] = [{"cCode": km, "iAmount_f": FX_AMOUNT}]
    else:
        body = receipt_body(day, ctl, key) if kind == "ar_receipt" else payment_body(day, ctl)
        body["lines"] = [{"iType": 0, "cKm": ctl, "iAmt_f": FX_AMOUNT}]
    _fx_head(body["head"], key, rate)
    return body


# ---- 汇率 ----


def read_rate(ctx: SeedContext) -> Decimal | None:
    rows = ctx.rows(rate_sql(ctx.day))
    text = rows[0].get("rate", "").strip() if rows else ""
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise SeedError("汇率读出来不是数：" + text) from exc
    return value.normalize() if value > 0 else None


def need_rate(ctx: SeedContext) -> Decimal:
    rate = read_rate(ctx)
    if rate is None:
        raise SeedError("前面的步骤 fx_rate 还没有造出 " + rate_code(ctx.day) + " 的记账汇率，先跑它")
    return rate


def _probe_rate(ctx: SeedContext) -> Probe:
    item = _ARCHIVE + ":" + rate_code(ctx.day)
    return Probe((item,), ()) if read_rate(ctx) is not None else Probe((), (item,))


def _apply_rate(ctx: SeedContext) -> tuple[int, int]:
    if read_rate(ctx) is not None:
        return 0, 1
    post_ok(ctx, "v1/archives/create", rate_body(ctx.day), "新增汇率 " + rate_code(ctx.day))
    need_rate(ctx)
    return 1, 0


# ---- 单据 ----


def _finder(key: str):
    _kind, flag, vouch, close, _side = FX_DOCS[key]
    sql = _CLOSE_FIND_SQL if close else _BILL_FIND_SQL

    def find(ctx: SeedContext) -> list[int]:
        return find_ids(ctx, sql.format(flag=flag, vouch=vouch, mark=quoted([marker(key)])))

    return find


def usd_bank(ctx: SeedContext) -> str:
    year, _period = period_of(ctx.day)
    rows = ctx.rows(_USD_BANK_SQL.format(year=year, name=quoted([CURRENCY])))
    code = rows[0].get("code", "").strip() if rows else ""
    if not code:
        raise SeedError("账套没有币种为" + CURRENCY + "的末级银行科目（1002 下）")
    return code


_USD_CTL_SQL = (
    "SELECT TOP (1) ccode AS code FROM code WHERE iyear = {year} AND bend = 1 AND ccode LIKE N'{prefix}%'"
    " AND cexch_name = {name} ORDER BY ccode"
)


def usd_control(ctx: SeedContext, side: str) -> str:
    # 外币单据的受控科目也要是同币种的（实测人民币受控科目报「第1行的科目与表头币种不一致」）；没有时退回原受控科目。
    year, _period = period_of(ctx.day)
    prefix = "1122" if side == "ar_receipt" else "2202"
    rows = ctx.rows(_USD_CTL_SQL.format(year=year, prefix=prefix, name=quoted([CURRENCY])))
    return rows[0].get("code", "").strip() if rows else ""


def _maker(key: str):
    kind, _flag, _vouch, close, side = FX_DOCS[key]

    def make(ctx: SeedContext) -> int:
        ctl = usd_control(ctx, side) or control_km(ctx, side)
        km = "" if close else bill_km(ctx, kind)
        body = fx_body(key, ctx.day, (ctl, km), need_rate(ctx))
        if close:
            # 收付款单的结算科目要是美元户（实测人民币银行科目报「币种与科目不一致」）。
            body["head"]["cCode"] = usd_bank(ctx)
        return create_doc(ctx, body)

    return make


def _verifier(key: str):
    kind, _flag, _vouch, close, _side = FX_DOCS[key]
    sql = _CHECKED_SQL["close" if close else "bill"]

    def after(ctx: SeedContext, doc_id: int) -> None:
        rows = ctx.rows(sql.format(id=safe_id(doc_id)))
        if rows and rows[0].get("n", "0").strip() not in ("", "0"):
            return
        post_ok(ctx, "v1/vouchers/verify", {"type": kind, "id": doc_id, "action": "verify"}, "审核 " + kind + " " + str(doc_id))

    return after


def _fx_doc(key: str, title: str) -> Step:
    return doc_step(Doc(key, title, _finder(key), _maker(key), _verifier(key)))


STEPS = (
    Step("fx_rate", "美元本月记账汇率（已有沿用）", _probe_rate, _apply_rate),
    _fx_doc("fx_ar_receipt", "美元收款单（审核，不制单）"),
    _fx_doc("fx_ar_bill", "美元应收单（审核）"),
    _fx_doc("fx_ap_payment", "美元付款单（审核，不制单）"),
    _fx_doc("fx_ap_bill", "美元应付单（审核）"),
)
