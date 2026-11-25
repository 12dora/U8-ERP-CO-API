"""接线：用例（mb_cases）、自定义步骤（MB_OPS）、补偿（MB_CLEANUP），经 dry_suite 并入 catalog、ops_run 和 runner。

用例说明见各模块：meta_fields（字段标签 meta/fields）、doc_search（单据搜索）、batch_read（批量读取）、
idem_routes（全部写路由的幂等键）。只有 idem_routes 的用例会写账套，失败时按这里的补偿尽力撤回。
"""

from __future__ import annotations

from co.client.u8co_client import U8CoError
from co.e2e.casespec.gl_voucher_bodies import slot
from co.e2e.casespec.compensate import compensate
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec
from co.e2e.casespec.dry_ops import send
from co.e2e.casespec.batch_read import MB_BATCH_OPS, batch_cases
from co.e2e.casespec.idem_routes import GL, MB_IDEM_OPS, SO_KEY, WF_FLAG, WITHDRAW, idem_cases
from co.e2e.casespec.meta_fields import MB_META_OPS, meta_fields_cases
from co.e2e.casespec.doc_search import MB_SEARCH_OPS, search_case
from co.e2e.casespec.gl_voucher_sql import parse_key

__all__ = ["MB_CLEANUP", "MB_OPS", "mb_cases"]

MB_OPS = {**MB_META_OPS, **MB_SEARCH_OPS, **MB_BATCH_OPS, **MB_IDEM_OPS}


def mb_cases() -> list[CaseSpec]:
    return meta_fields_cases() + [search_case()] + batch_cases() + idem_cases()


def _try(env: Env, route: str, body: dict) -> str:
    try:
        return str(send(env, route, body)[0])
    except (U8CoError, OSError, RuntimeError, ValueError) as exc:
        return "出错 " + str(exc)[:60]


def _so(env: Env, _case: str) -> str:
    return compensate(env, ((SO_KEY, "sale_order", True),))


def _gl(env: Env, case: str) -> str:
    # 作废再删除（已作废 / 已删除时 409 / 404 属正常）。
    try:
        found = parse_key(env.bag.get(slot(CaseSpec(case, "")), ""))
    except ValueError:
        return "没有新建的凭证"
    ident = {"period": found.period, "sign": found.sign, "no": found.no}
    return "凭证补偿：" + "，".join(op + " " + _try(env, GL + op, ident) for op in ("void", "delete"))


def _wf(env: Env, _case: str) -> str:
    raw = env.bag.get(WF_FLAG, "")
    if not raw.isdigit():
        return "没有提交过审批"
    return "撤销提交 " + _try(env, WITHDRAW, {"type": "qm_product_check", "id": int(raw)})


MB_CLEANUP = {"idempotency-sale-order": _so, "idempotency-gl-void": _gl, "idempotency-wf-submit": _wf}
