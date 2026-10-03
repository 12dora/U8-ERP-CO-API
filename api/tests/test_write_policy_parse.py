"""写入策略文件的解析与校验（与桥 WritePolicyParse.cs 同一套规则）。不访问网络。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from u8co_api.write_policy_parse import OP_NAMES, PolicyError, parse

_AT = datetime(2026, 10, 3, tzinfo=timezone.utc)

FULL = {
    "comment": "示例策略",
    "version": 1,
    "reloadSeconds": 5,
    "freeze": {"global": False, "accounts": ["801"], "reason": "月末结账", "comment": "临时"},
    "windows": [{"days": "1-5", "start": "09:00", "end": "18:00"}],
    "denyDates": ["2026-10-31"],
    "license": {"maxConcurrentLogins": 1, "holdWritesWhen": ["near", "full"]},
    "defaults": {"writesPerMinute": 10, "writesPerDay": 200, "maxLines": 200, "maxAmount": 0},
    "unlisted": "deny",
    "accounts": {
        "comment": "账套",
        "801": {
            "operators": {"allow": [], "deny": ["svc01"]},
            "quotas": {"writesPerMinute": 8, "maxLines": 80, "maxAmount": 5000000},
            "allow": [{"type": "sale_order", "ops": ["create"]}, {"type": "*", "ops": ["*"], "comment": "其余"}],
        },
    },
}


def _parse(data: object):
    return parse(data if isinstance(data, str) else json.dumps(data), _AT)


def test_full_example_parses() -> None:
    snap = _parse(FULL)
    assert (snap.version, snap.reload_seconds, snap.loaded_at) == (1, 5, _AT)
    assert snap.freeze_accounts == ("801",) and snap.freeze_reason == "月末结账"
    assert snap.deny_dates == frozenset({"2026-10-31"})
    assert snap.unlisted_allow is False
    account = snap.account("801")
    assert account is not None
    # 账套 quotas 按键覆盖 defaults，没写的键沿用 defaults。
    assert account.limits.per_minute == 8 and account.limits.per_day == 200
    assert account.limits.max_lines == 80 and account.limits.max_amount == Decimal(5000000)
    assert snap.limits("906").max_lines == 200
    assert account.allows("sale_order", "create") and account.allows("gl", "post")
    assert not account.operators.permits("SVC01")


def test_minimal_policy_defaults() -> None:
    snap = _parse({"version": 1})
    assert snap.reload_seconds == 2
    assert snap.windows is None and snap.unlisted_allow is False
    assert snap.accounts == {} and snap.defaults.max_lines == 0


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        ("{", "不是有效的 JSON"),
        ("[]", "必须是 JSON 对象"),
        ({}, "version 必须是 1"),
        ({"version": 2}, "version 必须是 1"),
        ({"version": True}, "version 必须是 1"),
        ({"version": 1.0}, "version 必须是 1"),
        ({"version": 1, "extra": 1}, "含未知字段 extra"),
        ({"version": 1, "comment": 3}, "comment 必须是字符串"),
        ({"version": 1, "reloadSeconds": 0}, "reloadSeconds 必须是 1 到 3600 的整数"),
        ({"version": 1, "freeze": {"global": "yes"}}, "freeze.global 必须是 true 或 false"),
        ({"version": 1, "freeze": {"accounts": ["9050"]}}, "freeze.accounts 的每一项必须是 3 位数字的账套号"),
        ({"version": 1, "freeze": {"why": "x"}}, "含未知字段 freeze.why"),
        ({"version": 1, "windows": []}, "windows 不能是空数组"),
        ({"version": 1, "windows": [{"days": "1-5", "start": "08:00", "end": "08:00"}]}, "不支持跨午夜"),
        ({"version": 1, "windows": [{"days": "1-8", "start": "08:00", "end": "09:00"}]}, "windows[0].days 不合法"),
        ({"version": 1, "windows": [{"days": "5-1", "start": "08:00", "end": "09:00"}]}, "反了"),
        ({"version": 1, "windows": [{"days": "1", "start": "8:00", "end": "09:00"}]}, "必须是 HH:MM"),
        ({"version": 1, "windows": [{"start": "08:00", "end": "09:00"}]}, "缺少 windows[0].days"),
        ({"version": 1, "denyDates": ["2026-02-30"]}, "denyDates 的每一项必须是 yyyy-MM-dd"),
        ({"version": 1, "denyDates": ["2026-1-5"]}, "denyDates 的每一项必须是 yyyy-MM-dd"),
        ({"version": 1, "license": {"holdWritesWhen": ["ok"]}}, "license.holdWritesWhen 只能是"),
        ({"version": 1, "license": {"maxConcurrentLogins": 65}}, "license.maxConcurrentLogins"),
        ({"version": 1, "defaults": {"maxAmount": -1}}, "defaults.maxAmount 必须在 0 到"),
        ({"version": 1, "defaults": {"maxAmount": "5"}}, "defaults.maxAmount 必须是数字"),
        ({"version": 1, "defaults": {"maxLines": 1.5}}, "defaults.maxLines 必须是"),
        ({"version": 1, "unlisted": "maybe"}, "unlisted 只能是"),
        ({"version": 1, "accounts": {"95": {}}}, "accounts 的键必须是 3 位数字的账套号"),
        ({"version": 1, "accounts": {"801": []}}, "accounts.801 必须是对象"),
        ({"version": 1, "accounts": {"801": {"limits": {}}}}, "含未知字段 accounts.801.limits"),
        ({"version": 1, "accounts": {"801": {"operators": {"allow": [""]}}}}, "只能含非空字符串"),
        ({"version": 1, "accounts": {"801": {"allow": [{"type": "SaleOrder", "ops": ["*"]}]}}}, "小写的类型名"),
        ({"version": 1, "accounts": {"801": {"allow": [{"type": "x", "ops": ["sell"]}]}}}, "含未知操作 'sell'"),
        ({"version": 1, "accounts": {"801": {"allow": [{"type": "x", "ops": []}]}}}, "不能是空数组"),
        ({"version": 1, "accounts": {"801": {"allow": [{"type": "x"}]}}}, "缺少 accounts.801.allow[0].ops"),
        ({"version": 1, "accounts": {"801": {"allow": {"type": "x"}}}}, "accounts.801.allow 必须是数组"),
        ('{"version": 1, "reloadSeconds": NaN}', "不是有效的 JSON"),
    ],
)
def test_invalid_policies_are_rejected_with_a_key_path(data: object, fragment: str) -> None:
    with pytest.raises(PolicyError) as caught:
        _parse(data)
    assert str(caught.value).startswith("写入策略 ")
    assert fragment in str(caught.value)


def test_op_vocabulary_is_the_closed_bridge_set() -> None:
    assert OP_NAMES == (
        "create", "update", "delete", "verify", "unverify", "close", "open", "generate", "lock", "unlock",
        "workflow", "writeoff", "voucher", "post", "process", "other",
    )  # fmt: skip


def test_windows_and_deny_dates() -> None:
    window = {"days": "1-5,7", "start": "07:00", "end": "18:30"}
    snap = _parse({"version": 1, "windows": [window], "denyDates": ["2026-10-05"]})
    assert snap.window_open(datetime(2026, 10, 2, 7, 0))  # 星期五 07:00 含
    assert not snap.window_open(datetime(2026, 10, 2, 18, 30))  # 不含 end
    assert not snap.window_open(datetime(2026, 10, 3, 9, 0))  # 星期六
    assert snap.window_open(datetime(2026, 10, 4, 9, 0))  # 星期日
    assert not snap.window_open(datetime(2026, 10, 5, 9, 0))  # denyDates


def test_operator_lists_ignore_case_and_blank() -> None:
    data = {"version": 1, "accounts": {"801": {"operators": {"allow": [" Demo "], "deny": ["op002"]}}}}
    ops = _parse(data).account("801").operators
    assert ops.permits("DEMO") and not ops.permits("op002") and not ops.permits("other") and not ops.permits("")
