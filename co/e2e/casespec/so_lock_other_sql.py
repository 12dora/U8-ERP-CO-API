"""销售订单「别的操作员锁定」用例（so_lock_other）的 SQL：直接改写表头锁定人 cLocker，模拟别的操作员锁定。

挑单复用 so_lock_sql.lock_so_sql（未审核、未锁定、未关闭、不受审批流控制的销售订单）。写语句都只动这一张订单的
cLocker，并带着改写前应有的状态作条件，回 @@ROWCOUNT 供核对；拼进去的只有校验过范围的主键和常量。操作员姓名多为中文，
不拼进 SQL：「姓名后加一个字符」由 SQL 在桥写下的 cLocker 上直接追加。
lock_other_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

from co.e2e.casespec.so_lock_sql import lock_so_sql

# 冒充的别的操作员姓名（ASCII，不会与真实操作员同名）。
OTHER = "E2E-OTHER"
SUFFIX = "X"
WIDTH = 20
_HELD = "LTRIM(RTRIM(ISNULL(cLocker, N'')))"


def _id(doc_id: int) -> int:
    if type(doc_id) is not int or not 1 <= doc_id <= 2147483647:
        raise ValueError("单据主键不对")
    return doc_id


def _update(doc_id: int, assign: str, cond: str) -> str:
    return (
        "SET NOCOUNT ON;\n"
        f"UPDATE dbo.SO_SOMain SET cLocker = {assign} WHERE ID = {_id(doc_id)} AND {cond};\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def pick_sql() -> str:
    return lock_so_sql()


def locker_sql(doc_id: int) -> str:
    """一行：<锁定人（去两端空格，未锁定为空串）>|<长度>；单据不存在时没有行。"""
    return (
        "SET NOCOUNT ON;\n"
        f"SELECT N'ROW:' + {_HELD} + N'|' + CONVERT(varchar(4), LEN({_HELD})) AS v\n"
        f"FROM dbo.SO_SOMain WHERE ID = {_id(doc_id)};\n"
    )


def lock_as_other_sql(doc_id: int) -> str:
    """未锁定的订单写成被 OTHER 锁定。"""
    return _update(doc_id, f"N'{OTHER}'", f"{_HELD} = N''")


def append_sql(doc_id: int) -> str:
    """锁定人姓名后加 SUFFIX（只在姓名不足 WIDTH 个字符时）：模拟前缀相同、姓名多一个字的别的操作员。"""
    return _update(doc_id, f"{_HELD} + N'{SUFFIX}'", f"LEN({_HELD}) BETWEEN 1 AND {WIDTH - 1}")


def strip_sql(doc_id: int) -> str:
    """去掉 append_sql 加的最后一个字符，回到桥写下的姓名。"""
    return _update(doc_id, f"LEFT({_HELD}, LEN({_HELD}) - 1)", f"LEN({_HELD}) >= 2 AND {_HELD} LIKE N'%{SUFFIX}'")


def clear_sql(doc_id: int) -> str:
    """恢复成未锁定（挑单时 cLocker 为空）。"""
    return _update(doc_id, "NULL", f"{_HELD} <> N''")


def lock_other_samples() -> list[tuple[str, str]]:
    return [
        ("lock-other-pick", pick_sql()),
        ("lock-other-locker", locker_sql(100)),
        ("lock-other-set", lock_as_other_sql(100)),
        ("lock-other-append", append_sql(100)),
        ("lock-other-strip", strip_sql(100)),
        ("lock-other-clear", clear_sql(100)),
    ]
