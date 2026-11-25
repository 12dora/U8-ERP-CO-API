"""审批流里的审批人和 e2e 操作员的人员对应。审批人编码不写死，从已发布审批流的 XML 现取。

操作员对应专用人员 data.OPERATOR_PERSON（不是审批人）：回归 wf-qm04-cycle 要求提交后操作员不是当前审批人。
造数自己的 QM03 审批（steps_docs_qc.approve_all）每轮先把当前待办改派给操作员对应的人员再同意，
对应谁都能走通。旧基线把操作员对应到了 QM04 首步审批人，重跑时把对应改成专用人员。

XML 在服务器上用 XQuery 拆成每个 Performer 一行（按文档顺序），只把审批人表达式取回来。
存的 XML 带 encoding="utf-8" 声明，转 xml 类型前要去掉；xml 方法要求 QUOTED_IDENTIFIER ON。
别名保持 p(n)、c(x)：写成三段名（p.n.value）会被 sqlguard 拒绝。
"""

from __future__ import annotations

import re

from co.e2e.seed import data
from co.e2e.seed.base import Probe, Step, create_archive, existing_codes, quoted, remember
from co.e2e.seed.context import SeedContext

RELEASES_SQL = """SET QUOTED_IDENTIFIER ON;
SELECT r.cBizObjectId AS biz,
  REPLACE(REPLACE(REPLACE(n.value('@id', 'nvarchar(4000)'), NCHAR(13), N' '), NCHAR(10), N' '), NCHAR(9), N' ')
    AS performer
FROM Table_WorkFlowRelease r
CROSS APPLY (SELECT CAST(STUFF(CAST(r.[XML] AS nvarchar(max)), 1,
  CHARINDEX(N'?>', CAST(r.[XML] AS nvarchar(max))) + 1, N'') AS xml)) c(x)
CROSS APPLY x.nodes('//*[local-name()="Performer"]') p(n)
WHERE r.Status = 0"""

# 操作员 → 人员。列名 cGUID、cPsn_Num、cUser_Id 以现场库为准；要改只改这两条。
LINK_CHECK_SQL = "SELECT cPsn_Num AS code FROM UserHrPersonContro WHERE cUser_Id = {operator}"
LINK_SET_SQL = (
    "IF EXISTS (SELECT 1 FROM UserHrPersonContro WHERE cUser_Id = {operator})\n"
    "  UPDATE UserHrPersonContro SET cPsn_Num = {person} WHERE cUser_Id = {operator}\n"
    "ELSE\n"
    "  INSERT INTO UserHrPersonContro (cGUID, cPsn_Num, cUser_Id) VALUES (NEWID(), {person}, {operator})"
)

# 审批人表达式形如 员工[|hr_hi_Person.cPsn_Num| = 'P001' OR ...]；列名大小写不定。
# 「$<StartUser>」这类占位符不是编码，字符集里没有 $ 和 <，自然排除。
_PSN = re.compile(
    r"\|hr_hi_Person\.cPsn_Num\|\s*=\s*(?:N?'|&apos;|&#39;)([A-Za-z0-9_-]{1,30})(?:'|&apos;|&#39;)",
    re.IGNORECASE,
)


def row_codes(performer: str) -> list[str]:
    return list(dict.fromkeys(_PSN.findall(performer)))


def performer_codes(rows: list[dict[str, str]]) -> list[str]:
    """所有启用审批流里出现的人员编码，按出现顺序去重。"""
    found: list[str] = []
    for row in rows:
        found.extend(code for code in row_codes(row.get("performer", "")) if code not in found)
    return found


def _performer_rows(ctx: SeedContext) -> list[dict[str, str]]:
    return ctx.rows(RELEASES_SQL)


def workflow_people(ctx: SeedContext) -> list[str]:
    return performer_codes(_performer_rows(ctx))


def _probe_performers(ctx: SeedContext) -> Probe:
    codes = workflow_people(ctx)
    have = existing_codes(ctx, "person", codes)
    return Probe(tuple(c for c in codes if c in have), tuple(c for c in codes if c not in have))


def _apply_performers(ctx: SeedContext) -> tuple[int, int]:
    codes = workflow_people(ctx)
    have = existing_codes(ctx, "person", codes)
    new = 0
    for code in codes:
        if code in have:
            continue
        if create_archive(ctx, data.person(code, "测试审批人" + code, data.DEPT_WORKFLOW)):
            new += 1
            remember(ctx, "person", code)
    ctx.state["workflow_people"] = codes
    return new, len(codes) - new


def _linked(ctx: SeedContext) -> str:
    rows = ctx.rows(LINK_CHECK_SQL.format(operator=quoted([ctx.operator])))
    return rows[0].get("code", "").strip() if rows else ""


def _is_operator_person(code: str) -> bool:
    return code.upper() == data.OPERATOR_PERSON.upper()


def _probe_link(ctx: SeedContext) -> Probe:
    linked = _linked(ctx)
    if _is_operator_person(linked):
        return Probe((ctx.operator + "→" + linked,), ())
    was = "（现为 " + linked + "）" if linked else ""
    return Probe((), (ctx.operator + "→" + data.OPERATOR_PERSON + was,))


def _apply_link(ctx: SeedContext) -> tuple[int, int]:
    linked = _linked(ctx)
    if _is_operator_person(linked):
        ctx.state["operator_person"] = linked
        return 0, 1
    person = quoted([data.OPERATOR_PERSON])
    ctx.exec(LINK_SET_SQL.format(operator=quoted([ctx.operator]), person=person))
    ctx.state["operator_person"] = data.OPERATOR_PERSON
    return 1, 0


STEPS = (
    Step("wf_people", "审批流审批人", _probe_performers, _apply_performers),
    Step("operator_link", "操作员对应专用人员（不是审批人）", _probe_link, _apply_link),
)
