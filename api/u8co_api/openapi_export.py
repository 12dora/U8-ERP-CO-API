"""离线导出 /v1/co 的 OpenAPI 文档，给静态接口参考页用。

运行：python -m u8co_api.openapi_export --out openapi.json [--accounts 801,802]

不读环境变量、不取 JWKS、不连桥：用空的信任项和空的桥地址在内存里建一个应用，
再调运行时同一个 co_document。和运行时文档只差两处：
info.description 的账套一行换成占位说明，servers 换成示例地址。
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from u8co_api import co_routes
from u8co_api.co_routes import co_document, describe
from u8co_api.co_routes_arap_proc import TAG_ARAP_PROC
from u8co_api.co_routes_mgmt import TAG_MGMT  # 经营管理查询
from u8co_api.co_routes_openings import TAG_OPENING
from u8co_api.co_routes_gl_arc import TAG_ARC, TAG_GL, TAG_STOCK
from u8co_api.co_routes_periods import TAG_IA, TAG_PERIOD
from u8co_api.co_routes_reports import TAG_REPORT
from u8co_api.co_table import TAG_AUDIT, TAG_CO, TAG_EDIT, TAG_FLOW, TAG_GEN, TAG_READ
from u8co_api.config import Settings, parse_accounts
from u8co_api.main import AppDeps, create_app

PLACEHOLDER_SERVER = {"url": "https://u8co.example.com", "description": "示例地址，换成你的部署地址"}
PLACEHOLDER_ACCOUNTS = "允许的账套以部署配置为准（环境变量 U8CO_ACCOUNTS）；名单为空时所有业务调用都返回 403。"

# 分组说明。顺序即参考页侧栏顺序；没登记的分组排在后面，说明留空。
_TIER2 = "\n\n第二级写入：默认关闭，打开后只对测试账套开放。"
TAG_NOTES = (
    (TAG_CO, "登录检查、健康检查、字段元数据与字段标签、幂等结果查询、有效权限；销售订单和发货单的专用审核；应收应付核销与制单。"),
    (TAG_READ, "读取单据、批量读取、单据列表与查询、附件列表、票据读取。只读。"),
    (TAG_AUDIT, "按单据类型审核或弃审（vouchers/verify）。"),
    (TAG_EDIT, "新增、修改、删除、关闭、打开和锁定单据。"),
    (TAG_GEN, "按来源单据参照生成下游单据；公司间按卖方单据生成买方单据。"),
    (TAG_FLOW, "审批流：状态、历史、待办、提交、撤销、同意、不同意、退回、弃审、重新提交。"),
    (TAG_GL, "总账凭证的读取、列表、新增、修改、作废、审核、签字和删除。"),
    (TAG_ARC, "基础档案的读取、列表、名称解析、新增、修改和删除。"),
    (TAG_STOCK, "现存量查询。只读。"),
    (TAG_REPORT, "月结状态、科目余额、往来余额、账龄、物料清单等只读报表。"),
    (TAG_OPENING, "采购、存货核算期初记账和取消期初记账；应收、应付期初单据。" + _TIER2),
    (TAG_PERIOD, "各模块的月末结账、取消结账和逐月结账。" + _TIER2),
    (TAG_IA, "存货核算的正常单据记账、恢复记账、期末处理和取消期末处理。" + _TIER2),
    (TAG_ARAP_PROC, "应收冲应付、应付冲应收、并账、红票对冲、汇兑损益，及其取消和制单。" + _TIER2),
    (TAG_MGMT, "多账套的月结状态、利润表、销售毛利、往来账期、资金存货和概览，可合并抵销。\n\n另需经营管理权限。"),
)

_METHODS = {"get", "post", "put", "delete", "patch", "head", "options", "trace"}


def offline_settings(accounts: tuple[str, ...] = ()) -> Settings:
    # 空信任项：不会取 JWKS。空桥地址：build_bridge 返回 None，不会连桥。审计关掉，不往 stdout 写。
    return Settings(trust=(), bridge_url="", bridge_secret="", accounts=accounts, audit_log="off")


def build_document(accounts: tuple[str, ...] = ()) -> dict:
    logger = logging.getLogger("u8co.api")
    old = logger.disabled
    # 离线构建必然缺配置，不打「配置不完整」的告警。
    logger.disabled = True
    try:
        app = create_app(AppDeps(settings=offline_settings(accounts)))
    finally:
        logger.disabled = old
    document = co_document(app)
    document.setdefault("info", {})["description"] = _description(accounts)
    document["servers"] = [dict(PLACEHOLDER_SERVER)]
    document["tags"] = _tags(document)
    return document


def _description(accounts: tuple[str, ...]) -> str:
    if accounts:
        return describe(accounts)
    return co_routes._DESCRIPTION.replace("{accounts}", PLACEHOLDER_ACCOUNTS)


def _tags(document: dict) -> list[dict]:
    used = _used_tags(document)
    notes = dict(TAG_NOTES)
    ordered = [name for name, _ in TAG_NOTES if name in used]
    ordered += sorted(used - set(ordered))
    return [{"name": name, "description": notes.get(name, "")} for name in ordered]


def _used_tags(document: dict) -> set[str]:
    found: set[str] = set()
    for operation in operations(document):
        found.update(operation.get("tags") or [])
    return found


def operations(document: dict) -> list[dict]:
    found: list[dict] = []
    for item in document.get("paths", {}).values():
        for method, operation in item.items():
            if method in _METHODS and isinstance(operation, dict):
                found.append(operation)
    return found


def dumps(document: dict) -> str:
    return json.dumps(document, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="离线导出 /v1/co 的 OpenAPI 文档")
    parser.add_argument("--out", required=True, help="输出的 JSON 文件")
    parser.add_argument("--accounts", default="", help="写进说明的账套，逗号分隔；缺省写占位说明")
    args = parser.parse_args(argv)
    document = build_document(parse_accounts(args.accounts))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dumps(document), encoding="utf-8")


if __name__ == "__main__":
    main()
