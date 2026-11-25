"""分包票据登记用例（notes_split）的回读 SQL。

只读；表名、列名是常量。票据号只接受用例自己编的 E2E 加数字（notes_sql.safe_note），子票号只接受 1 到 15 位的正整数。
输出一行 ROW:<值>，多个值用 | 分隔；文本值里的 | 换成 /。
"""

from __future__ import annotations

from co.e2e.casespec.arap_proc_sql import _count, _money, _row
from co.e2e.casespec.notes_sql import _join, _top, safe_note

# 子票号上限，同接口 sub_start / sub_end（15 位）。
_SERIAL_MAX = 999999999999999


def safe_serial(value: int) -> int:
    if type(value) is not int or not 1 <= value <= _SERIAL_MAX:
        raise ValueError("子票号不合法")
    return value


def split_state_sql(no: str, start: int, end: int) -> str:
    """一行（按票据号与登记的子票区间）：<票据张数>|<bsubpackage>|<csubnostart>|<csubnoend>|<票面>|<余额>
    |<可用区间行数>|<最小起号>|<最大止号>|<可用金额合计>|<可用本币金额合计>
    |<与票据对上的收款单张数（iID = iCloseID、48、来源 C / 50、票据号和来源号都是「票据号-起号-止号」）>
    |<票据号为「票据号-起号-止号」的收付款单张数>|<对上的收款单里已审核的张数>|<票据号只是票据号（不带区间）的收付款单张数>。

    可用区间按 cNoteLink = AR50 + 票据号查（票据删掉后也能查到残留行）。"""
    key = safe_note(no)
    lo, hi = safe_serial(start), safe_serial(end)
    if lo > hi:
        raise ValueError("子票区间不合法")
    tag = f"{key}-{lo}-{hi}"
    frm = f"FROM dbo.AP_Note AS n WHERE n.cFlag = N'AR' AND n.cVouchType = N'50' AND n.cVouchID = N'{key}'"
    rng = f"FROM dbo.Ap_Note_AvailRange AS a WHERE a.cNoteLink = N'AR50{key}'"
    tied = (
        "SELECT COUNT(*) FROM dbo.AP_Note AS n INNER JOIN dbo.Ap_CloseBill AS c ON c.iID = n.iCloseID"
        f" WHERE n.cFlag = N'AR' AND n.cVouchType = N'50' AND n.cVouchID = N'{key}' AND c.cVouchType = N'48'"
        " AND c.cFlag = N'AR' AND c.cSrcFlag = N'C' AND c.cCoVouchType = N'50'"
        f" AND c.cCoVouchID = N'{tag}' AND c.cNoteNo = N'{tag}'"
    )
    parts = (
        _count(f"SELECT COUNT(*) {frm}"),
        _top("CONVERT(int, ISNULL(n.bsubpackage, 0))", frm),
        _top("n.csubnostart", frm),
        _top("n.csubnoend", frm),
        _money(f"(SELECT TOP (1) n.iAmount {frm})"),
        _money(f"(SELECT TOP (1) n.iRAmount {frm})"),
        _count(f"SELECT COUNT(*) {rng}"),
        _top("MIN(a.cavailstart)", rng),
        _top("MAX(a.cavailend)", rng),
        _money(f"(SELECT SUM(a.iavailamount) {rng})"),
        _money(f"(SELECT SUM(a.iavailamount_local) {rng})"),
        _count(tied),
        _count(f"SELECT COUNT(*) FROM dbo.Ap_CloseBill AS c WHERE c.cCoVouchType = N'50' AND c.cNoteNo = N'{tag}'"),
        _count(tied + " AND LTRIM(RTRIM(ISNULL(c.cCheckMan, N''))) <> N''"),
        _count(f"SELECT COUNT(*) FROM dbo.Ap_CloseBill AS c WHERE c.cCoVouchType = N'50' AND c.cNoteNo = N'{key}'"),
    )
    return _row(_join(parts))


def split_reg_samples() -> list[tuple[str, str]]:
    return [("ntr-split-state", split_state_sql("E2E2026100209000005", 1309652951, 1309662950))]
