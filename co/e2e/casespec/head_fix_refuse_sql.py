"""voucher-head-fix 用例里「应被拒绝」的发货单的挑单 SQL：表头业务员为空、原值不可回滚、未审核。数据驱动，不写死编码。

公共片段（新地址 / 新业务员的取法、可回滚条件、skip）来自 head_fix_sql。不出现 sqlguard 拒绝的词。
"""

from __future__ import annotations

from co.e2e.casespec.head_fix_sql import _VALID, _applies, _plain, _reversible_addr, _reversible_person, _skip


def empty_person_pick_sql(skip: tuple[str, ...] = ()) -> str:
    """已审核发货单，表头业务员为空：改业务员应被 person_empty 拒绝（之后无法回滚）。输出 发货单 id、占位、一个可用的业务员。"""
    return (
        "SET NOCOUNT ON;\n"
        "SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), d.DLID) + NCHAR(9) + N'x' + NCHAR(9) + np.c AS v\n"
        "FROM dbo.DispatchList AS d\n"
        "CROSS APPLY (SELECT TOP (1) p.cPersonCode AS c FROM dbo.Person AS p\n"
        f"  WHERE p.cDepCode = d.cDepCode AND {_plain('p.cPersonCode', 20)} AND {_VALID}\n"
        "  ORDER BY p.cPersonCode) AS np\n"
        "WHERE d.cVerifier IS NOT NULL AND LTRIM(RTRIM(d.cVerifier)) <> N''\n"
        "  AND (d.cPersonCode IS NULL OR LTRIM(RTRIM(d.cPersonCode)) = N'')\n"
        f"  AND {_reversible_addr()}\n"
        f"{_skip(skip)}ORDER BY d.DLID DESC;\n"
    )


def not_reversible_pick_sql(kind: str, skip: tuple[str, ...] = ()) -> str:
    """已审核发货单，原值不可回滚：kind=addr 时原收货地址非空、却不在客户的收货地址档案里；kind=person 时原业务员非空、
    却不在 Person、或在发货日期无效、或部门不是发货单部门。改这一项应被 not_reversible 拒绝。
    输出 发货单 id、新地址（addr 才有，否则占位）、新业务员（person 才有，否则占位）。"""
    if kind not in ("addr", "person"):
        raise ValueError("kind 只能是 addr 或 person")
    if kind == "addr":
        cells, cond = "na.c + NCHAR(9) + N'x'", (
            f"{_plain('d.caddcode', 30)} AND {_reversible_person()}\n"
            "  AND NOT EXISTS (SELECT 1 FROM dbo.CusDeliverAdd AS ra WHERE ra.cCusCode = d.cCusCode AND ra.cAddCode = d.caddcode)"
        )
    else:
        cells, cond = "N'x' + NCHAR(9) + np.c", (
            f"{_plain('d.cPersonCode', 20)} AND {_reversible_addr()}\n  AND NOT {_reversible_person()}"
        )
    return (
        "SET NOCOUNT ON;\n"
        f"SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), d.DLID) + NCHAR(9) + {cells} AS v\n"
        "FROM dbo.DispatchList AS d\n"
        + _applies(False)
        + "WHERE d.cVerifier IS NOT NULL AND LTRIM(RTRIM(d.cVerifier)) <> N''\n"
        f"  AND {cond}\n"
        f"{_skip(skip)}ORDER BY d.DLID DESC;\n"
    )


def unaudited_pick_sql(skip: tuple[str, ...] = ()) -> str:
    """未审核发货单（审核人为空）：改它应被 not_audited 拒绝（先于其它检查，预演也一样）。原值只要能当 expect 用
    （地址为空或不超长，业务员不超长）；输出 发货单 id、新地址、新业务员。"""
    return (
        "SET NOCOUNT ON;\n"
        "SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), d.DLID) + NCHAR(9) + na.c + NCHAR(9) + np.c AS v\n"
        "FROM dbo.DispatchList AS d\n"
        + _applies(False)
        + "WHERE (d.cVerifier IS NULL OR LTRIM(RTRIM(d.cVerifier)) = N'')\n"
        f"  AND (d.caddcode IS NULL OR ({_plain('d.caddcode', 30)})) AND {_plain('d.cPersonCode', 20)}\n"
        f"{_skip(skip)}ORDER BY d.DLID DESC;\n"
    )


def refuse_samples() -> list[tuple[str, str]]:
    return [
        ("core-hf-empty-person", empty_person_pick_sql()),
        ("core-hf-not-reversible-addr", not_reversible_pick_sql("addr", ("1000000001",))),
        ("core-hf-not-reversible-person", not_reversible_pick_sql("person")),
        ("core-hf-unaudited", unaudited_pick_sql(("1000000001",))),
    ]
