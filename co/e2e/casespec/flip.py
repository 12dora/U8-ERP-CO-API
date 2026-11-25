"""临时改选项或造数据、跑完原样恢复的一组用例的登记汇总（catalog、ops_run、samples 各接一处）。

- openings_pu_unpost：openings-post-pu-unpost-refused、openings-post-pu-unpost（采购期初取消记账）
- exch_floating：archive-exchange-rate-floating（浮动汇率 cdate 写法，临时改汇率方式，独占）
- credit_flip：report-customer-credit-flip（翻转「信用余额控制用余额表」，独占）
- price_list：report-price-list-live（临时插入价格行核对价格表各列）
- so_lock_other：sale-order-lock-other（SQL 模拟别的操作员锁定；20 个字符比较规则）
公共的恢复骨架在 flip_util。
"""

from __future__ import annotations

from co.e2e.casespec.model import CaseSpec
from co.e2e.casespec.credit_flip import CREDIT_FLIP_OPS, credit_flip_cases
from co.e2e.casespec.credit_flip_sql import credit_flip_samples
from co.e2e.casespec.exch_floating import EXCH_FLOATING_OPS, exch_floating_cases
from co.e2e.casespec.exch_floating_sql import exch_floating_samples
from co.e2e.casespec.so_lock_other import LOCK_OTHER_OPS, lock_other_cases
from co.e2e.casespec.so_lock_other_sql import lock_other_samples
from co.e2e.casespec.price_list import PRICE_LIST_OPS, price_list_cases
from co.e2e.casespec.price_list_sql import price_list_samples
from co.e2e.casespec.openings_pu_unpost import PU_UNPOST_OPS, pu_unpost_cases
from co.e2e.casespec.openings_pu_unpost_sql import pu_unpost_samples

FLIP_OPS = {**PU_UNPOST_OPS, **EXCH_FLOATING_OPS, **CREDIT_FLIP_OPS, **PRICE_LIST_OPS, **LOCK_OTHER_OPS}


def flip_cases() -> list[CaseSpec]:
    return pu_unpost_cases() + lock_other_cases() + price_list_cases() + exch_floating_cases() + credit_flip_cases()


def flip_samples() -> list[tuple[str, str]]:
    return (
        pu_unpost_samples()
        + exch_floating_samples()
        + credit_flip_samples()
        + price_list_samples()
        + lock_other_samples()
    )
