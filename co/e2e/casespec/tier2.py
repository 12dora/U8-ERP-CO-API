"""第二级写入（复现 U8 界面 SQL 的写入）在本通道不可用的判断。不连接服务器。

桥的第二级写入有两道闸门，都在登录 U8 之前：总开关 enableReplicatedWrites 未打开时 403 feature_disabled；
打开后账套不在 testAccounts 里时 403 test_account_only。用例把这两种拒绝同样看作「本通道没有第二级写入」。
"""

from __future__ import annotations

TEST_ONLY = "test_account_only"
FEATURE_DISABLED = "feature_disabled"
TIER2_REFUSALS = frozenset({TEST_ONLY, FEATURE_DISABLED})
TIER2_TEXT = "403 feature_disabled / test_account_only"


def tier2_refused(status: int, payload: dict) -> bool:
    """403 feature_disabled 或 test_account_only：本通道没有第二级写入。"""
    return status == 403 and isinstance(payload, dict) and payload.get("code") in TIER2_REFUSALS


def refusal_verdict(payload: dict, text: str, what: str) -> tuple[str, str]:
    """*-test-only 用例在已确认 tier2_refused 之后的判定：test_account_only 还要核对原文，feature_disabled 不核对。"""
    code = payload.get("code")
    if code == TEST_ONLY and text not in str(payload.get("message")):
        return "FAIL", f"403 test_account_only 的原文应含「{text}」，实际 {payload.get('message')}"[:240]
    return "PASS", f"非测试账套{what}：登录前 403 {code}"
