"""用例：修改、关闭、参照生单，以及新的读取类型。"""

from __future__ import annotations

from co.e2e.casespec.model import CaseSpec
from co.e2e.casespec.pu_order_flow import pu_cases
from co.e2e.casespec.sale_order_flow import sale_cases
from co.e2e.casespec.stock_doc_edit import stock_cases


def edit_close_gen_cases() -> list[CaseSpec]:
    return sale_cases() + pu_cases() + stock_cases()
