"""库存、采购、销售的审核和新建，以及按类型读取。"""

from __future__ import annotations

from dataclasses import dataclass

from co.e2e.casespec.model import CaseSpec, Step, call, call_made, read_step
from co.e2e.casespec.sql_load import LOAD_SOURCES

_TITLES = {
    "sale_order": "销售订单",
    "dispatch": "发货单",
    "purchase_order": "采购订单",
    "arrival": "到货单",
    "purchase_in": "采购入库单",
    "other_in": "其他入库单",
    "other_out": "其他出库单",
    "product_in": "产成品入库单",
    "material_out": "材料出库单",
    "sale_out": "销售出库单",
    "qm_incoming_inspect": "来料报检单",
    "qm_product_inspect": "产品报检单",
    "qm_incoming_check": "来料检验单",
    "qm_product_check": "产品检验单",
    "qm_incoming_reject": "来料不良品处理单",
    "qm_product_reject": "产品不良品处理单",
    "qm_other_inspect": "其他报检单",
    "qm_other_check": "其他检验单",
    "transfer": "调拨单",
    "sale_invoice": "销售发票",
    "purchase_invoice": "采购发票",
    "production_order": "生产订单",
    "ar_receipt": "收款单",
    "ap_payment": "付款单",
    "ar_bill": "应收单",
    "ap_bill": "应付单",
}


def _load_name(kind: str) -> str:
    names = {
        "transfer": "load-transfer",
        "sale_invoice": "load-sale-invoice",
        "purchase_invoice": "load-purchase-invoice",
        "production_order": "load-production-order",
        "ar_receipt": "load-ar-receipt",
        "ap_payment": "load-ap-payment",
        "ar_bill": "load-ar-bill",
        "ap_bill": "load-ap-bill",
    }
    return names.get(kind, "load-" + kind)


# 审核与弃审一对步骤的入参。收成一个对象，避免函数形参超过 5 个。
@dataclass(frozen=True)
class _VerifyPair:
    name: str
    title: str
    need: str
    kind: str
    read_name: str
    optional: bool = False


def _verify_pair(spec: _VerifyPair) -> CaseSpec:
    fields_on = (("type", spec.kind), ("id", "$id"), ("action", "verify"))
    fields_off = (("type", spec.kind), ("id", "$id"), ("action", "unverify"))
    steps = (
        call("/v1/vouchers/verify", fields_on),
        read_step(spec.read_name, "verified"),
        call("/v1/vouchers/verify", fields_off),
        read_step(spec.read_name, "cleared"),
    )
    return CaseSpec(spec.name, spec.title, (spec.need,), steps, True, spec.optional)


def _reverse_pair(name: str, title: str, need: str, kind: str, read_name: str) -> CaseSpec:
    # 已审单据先弃审再审核，结束时回到原状态（随后整库还原）。
    fields_off = (("type", kind), ("id", "$id"), ("action", "unverify"))
    fields_on = (("type", kind), ("id", "$id"), ("action", "verify"))
    steps = (
        call("/v1/vouchers/verify", fields_off),
        read_step(read_name, "cleared"),
        call("/v1/vouchers/verify", fields_on),
        read_step(read_name, "verified"),
    )
    return CaseSpec(name, title, (need,), steps, True, True)


def _create_steps(kind: str, read_name: str, checks: tuple) -> tuple[Step, ...]:
    on = (("type", kind), ("id", "$id"), ("action", "verify"))
    off = (("type", kind), ("id", "$id"), ("action", "unverify"))
    return (
        Step("create", sql_name=kind, checks=checks),
        Step("readcode", sql_name=read_name, id_from="made"),
        call_made("/v1/vouchers/verify", on),
        read_step(read_name, "verified", "made"),
        call_made("/v1/vouchers/delete", (("type", kind), ("id", "$id")), (), 409, "state_mismatch"),
        call_made("/v1/vouchers/verify", off),
        read_step(read_name, "cleared", "made"),
        call_made("/v1/vouchers/delete", (("type", kind), ("id", "$id")), (("deleted", "true"),)),
        call_made("/v1/vouchers/load", (("type", kind), ("id", "$id")), (), 404, "not_found"),
    )


def _create(name: str, title: str, needs: tuple[str, ...], kind: str, read_name: str) -> CaseSpec:
    plain = (("code", "$nonempty"),)
    checks = plain + (("code#prefix", "yes"),) if kind == "other_in" else plain
    return CaseSpec(name, title, needs, _create_steps(kind, read_name, checks), True)


# 报检单（只读、没有审批流）：另核来源单据类型、有明细、不带 wf。
_INSPECT_SOURCES = {"qm_incoming_inspect": "arrival", "qm_product_inspect": "production_order"}


# 其他报检单没有来源（source 为 null）、其他检验单不接审批流：两种都不带 wf。
_OTHER_QM = {
    "qm_other_inspect": (("source", ""), ("lines#len", "$gt0"), ("wf", "")),
    "qm_other_check": (("wf", ""),),
}


def _load_checks(kind: str) -> tuple:
    checks = (("code", "$nonempty"), ("head#nonempty", "yes"))
    if kind in _OTHER_QM:
        return checks + _OTHER_QM[kind]
    source = _INSPECT_SOURCES.get(kind)
    if source is None:
        return checks
    return checks + (("source.type", source), ("source.id", "$gt0"), ("lines#len", "$gt0"), ("wf", ""))


def _loads() -> list[CaseSpec]:
    found = []
    for key, _table, _col, _where in LOAD_SOURCES:
        kind = key[len("load_") :]
        fields = (("type", kind), ("id", "$id"))
        step = call("/v1/vouchers/load", fields, _load_checks(kind))
        # 不良品处理单（QM05、QM06）接口不能新建，只有 U8 里做过的账套才有，没有时记 N/A。
        # 其他报检单、其他检验单同样只有在 U8 里做过的账套才有。
        optional = kind in ("qm_incoming_reject", "qm_product_reject", "qm_other_inspect", "qm_other_check")
        found.append(CaseSpec(_load_name(kind), "读取" + _TITLES[kind], (key,), (step,), True, optional))
    return found


def _negatives(bag: dict) -> list[CaseSpec]:
    doc = bag.get("qm04_a") or "1"
    opinion = call(
        "/v1/workflow/approve",
        (("type", "qm_product_check"), ("id", doc), ("opinion", "$long")),
        (),
        400,
        "bad_request",
    )
    blocked = Step("create", status=400, code="bad_request", sql_name="protected")
    foreign = Step(
        "callforeign",
        "/v1/vouchers/load",
        (("type", "sale_order"), ("id", "1")),
        403,
        "account_not_allowed",
    )
    return [
        CaseSpec("opinion-too-long", "审批意见过长", (), (opinion,), True),
        # 正文是其他入库；桥拒绝前不进 U8，标记写其他入库只为让列表断言避开。
        CaseSpec("create-protected", "新建含制单人", (), (blocked,), True, tags=frozenset(("probe", "w:other_in"))),
        CaseSpec("account-foreign-load", "读取名单外账套", (), (foreign, Step("audit")), False),
    ]


def doc_cases(bag: dict) -> list[CaseSpec]:
    rows = [
        _verify_pair(_VerifyPair("stock-09-verify", "其他出库审核与弃审", "other_out_open", "other_out", "rd09", True)),
        _reverse_pair("stock-09-reverse", "其他出库弃审与重新审核", "other_out_done", "other_out", "rd09"),
        _create("other-in-cycle", "其他入库新建审核删除", ("other_in_master", "vn_prefix"), "other_in", "rd08"),
        _create("other-out-cycle", "其他出库新建审核删除", ("other_out_master",), "other_out", "rd09"),
        _verify_pair(_VerifyPair("po-verify", "采购订单审核与弃审", "po_open", "purchase_order", "po")),
        _verify_pair(_VerifyPair("arrival-verify", "到货单审核与弃审", "arrival_open", "arrival", "arrival")),
        _create("sale-create", "销售订单新建审核删除", ("sale_master",), "sale_order", "sale"),
    ]
    return rows + _loads() + _negatives(bag)
