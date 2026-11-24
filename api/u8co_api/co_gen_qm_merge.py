"""产品检验单合并检验（qm_product_check 参照多条产品报检单行）的请求校验，与桥 QmMergeReq 一致。

source_ids 是 2 到 20 条产品报检单表体 AUTOID（可来自不同报检单，不重复），代替 id；表头规则同单行检验单生单。
lines 可省略（每条来源检验整行剩余数量、全部合格）；给出时与 source_ids 一一对应。
"""

from __future__ import annotations

from u8co_api.co_doctext import section
from u8co_api.co_gen_qm import check_head_only, check_parts, check_split

MERGE_KIND = "qm_product_check"
MERGE_SOURCE = "qm_product_inspect"
MERGE_MIN, MERGE_MAX = 2, 20
_ID_MAX = 2147483647
_LINE_KEYS = frozenset({"source_line_id", "quantity", "fregquantity", "fconquantiy", "fdisquantity"})

SOURCE_IDS_HELP = (
    "产品检验单合并检验（type=qm_product_check）：2 到 20 个产品报检单表体行 id（QMINSPECTVOUCHERS.AUTOID，"
    "不是报检单 ID），可来自不同报检单，不重复；代替 id。lines 可省略，给出时与 source_ids 一一对应"
)
# U8 枚举 QM.ProMergeCheckRule 里支持的编码（3 需求跟踪不支持），与桥 QmMergeRules 一致。
MERGE_RULES = frozenset({"0", "1", "2", "4", "5", "6", "7", "8"})
MERGE_RULES_HELP = (
    "合并检验的合并规则（U8 合并规则编码，只与 source_ids 一起给）：各来源这些项必须相同。缺省 [\"0\"]。"
    "0 自由项、1 生产部门、2 仓库、4 合同号、5 生产订单号、6 客户、7 生产批号、8 项目大类+项目；3 需求跟踪不支持"
)
SOURCE_IDS_OUT_HELP = (
    "销售发票参照多张发货单（请求用 sources）时，全部来源发货单 DLID（按请求顺序），source_id 是第一张；"
    "产品检验单合并检验时是请求的来源报检单表体行，source_id 是表头取用的那条来源的报检单 ID"
)
QM_MERGE_HELP = section(
    "产品检验单合并检验（qm_product_check + source_ids）",
    (
        "source_ids：2 到 20 个已审核产品报检单的表体行 id（AUTOID，不是报检单 ID），可来自不同报检单；不带 id",
        "各来源的存货、辅计量单位与换算率、批号、检验方式和检验类型必须相同；未生成过检验单，不是分拣后的报检行",
        "merge_rules：U8 合并规则编码，缺省 [\"0\"]（自由项相同）；另可选 1 生产部门、2 仓库、4 合同号、5 生产订单号、"
        "6 客户、7 生产批号、8 项目大类+项目，3 需求跟踪不支持",
        "lines 可省略：每条来源检验整行数量、全部合格",
        "lines 给出时与 source_ids 一一对应，每行可带 quantity、fRegQuantity、fConQuantiy、fDisQuantity，"
        "可带 source_line_id（必须等于对应的 source_ids）",
        "表头规则同单行产品检验单；表头数量是各来源之和，表头的来源信息取 AUTOID 最大的一条",
        "响应 source_id 是该条来源的报检单 ID，source_ids 是请求的来源行；vouchers/load 的 merge_sources 列出合并明细",
    ),
)


def _merge_line(raw: dict, source_id: int) -> None:
    row: dict = {}
    for key, value in raw.items():
        low = key.lower()
        if low in row:
            raise ValueError("字段重复 " + key)
        if low not in _LINE_KEYS:
            raise ValueError("不能设置字段 " + key)
        row[low] = value
    if "source_line_id" in row and row["source_line_id"] != source_id:
        raise ValueError("source_line_id 必须等于对应的 source_ids")
    if "quantity" in row:
        check_split(row)
    else:
        check_parts(row)


def _check_ids(source_ids: list[int]) -> None:
    if not MERGE_MIN <= len(source_ids) <= MERGE_MAX:
        raise ValueError(f"合并检验须选择 {MERGE_MIN} 到 {MERGE_MAX} 条报检单表体行")
    if any(type(one) is not int or not 1 <= one <= _ID_MAX for one in source_ids):
        raise ValueError("source_ids 的每一项必须是正整数")
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("来源报检单行重复")


def _check_rules(rules: list | None) -> None:
    if rules is None:
        return
    codes = [str(one) if type(one) is int else one for one in rules]
    for code in codes:
        if code not in MERGE_RULES:
            raise ValueError(f"不支持的合并规则 {code}")
    if len(set(codes)) != len(codes):
        raise ValueError("合并规则重复")


def check_qm_merge(
    kind: str,
    source: str,
    plain: tuple[int | None, object],
    source_ids: list[int] | None,
    body: tuple[dict | None, list | None, list | None],
) -> bool:
    """合并检验：只给产品检验单参照产品报检单；不与 id、sources 同时给；source_ids 不重复；lines 与之对齐。

    校验完返回 True（CoGenerateIn._shape_done 直接返回）。
    """
    head, lines, rules = body
    if source_ids is None:
        raise ValueError("merge_rules 只能与 source_ids 一起给出（产品检验单合并检验）")
    if kind != MERGE_KIND or source != MERGE_SOURCE:
        raise ValueError(
            "source_ids 只用于产品检验单合并检验（type=qm_product_check，source_type 省略或 qm_product_inspect）"
        )
    if plain[0] is not None or plain[1] is not None:
        raise ValueError("source_ids 不能与 id、sources 同时给出")
    _check_ids(source_ids)
    _check_rules(rules)
    check_head_only(head)
    if lines is None:
        return True
    if len(lines) != len(source_ids):
        raise ValueError("合并检验的 lines 可省略；给出时必须与 source_ids 一一对应")
    for index, raw in enumerate(lines):
        try:
            _merge_line(raw, source_ids[index])
        except ValueError as exc:
            raise ValueError(f"lines.{index}：{exc}") from exc
    return True
