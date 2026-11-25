"""档案写入的附加核对：接在 archive-position-write、archive-purchase-type-write、archive-sale-type-write、
archive-user-define-write 的新增修改删除一轮之后，作为同一用例的第二步（sql_name 为 arc_probe，fields 带 archive）。

- 货位：在空闲的一级货位下新增二级货位，回读上级 bPosEnd 应由 U8 改成 0、下级为 1；删除下级后上级应恢复为 1；
  再删上级，两条都不留。
- 采购类型、销售类型：新建一个类型并把 bdefau 改成 1，回读 bDefault：新类型为 1，其他类型的默认标志应被 U8 清掉。
  无论结果如何，先把原来的默认类型改回 1、新类型改回 0，再删除新类型，最后核对默认类型与开始时完全相同。
- 自定义项档案：档案值用汉字，长度正好等于使用长度 iLength（字符数），多一个字桥先 400；正好的应新增成功、
  库里档案值完整（U8 按字符计）。U8 拒绝或截断说明 U8 按字节计，用例失败并给出原文。

挑不到数据时这一步记 PASS 并注明跳过（不把已通过的新增修改删除一轮改成 N/A）。任何结果都先删掉本步建的档案。
"""

from __future__ import annotations

import datetime as dt

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arc_write_more import _send
from co.e2e.casespec.arc_write_more_sql import arc_more_pick_sql, safe_part
from co.e2e.casespec.arc_probe_sql import (
    define_count_sql,
    define_pick_sql,
    defaults_sql,
    pos_flags_sql,
    pos_pick_sql,
    safe_cjk,
)
from co.e2e.casespec.sqlexec import run_guarded

_NAME = "u8co e2e"
_DIGITS = "零一二三四五六七八九"
_FILL = "档"


class _Fail(Exception):
    pass


class _Run:
    """一次附加核对：发请求、查 SQL，记下本步建的档案（失败时按建的倒序删除）。"""

    def __init__(self, env: Env, spec: CaseSpec, archive: str) -> None:
        self.env, self.label, self.archive = env, spec.name, archive
        self.made: list[str] = []

    def cell(self, query: str) -> str:
        return first_cell(run_guarded(self.env, query, self.label)).strip()

    def parts(self, query: str, count: int) -> list[str] | None:
        cell = self.cell(query)
        parts = cell.split("|") if cell else []
        return parts if len(parts) == count else None

    def send(self, op: str, code: str, fields: dict | None = None) -> tuple[int, dict]:
        extra: dict = {"archive": self.archive, "code": code}
        if fields is not None:
            extra["fields"] = fields
        return _send(self.env, op, extra)

    def create(self, code: str, fields: dict) -> None:
        status, payload = self.send("create", code, fields)
        if status == 200:
            self.made.append(code)
            return
        raise _Fail(_said(f"新增 {code}", status, payload))

    def update(self, code: str, fields: dict) -> tuple[int, dict]:
        return self.send("update", code, fields)

    def delete(self, code: str) -> None:
        status, payload = self.send("delete", code)
        if status != 200 or payload.get("deleted") is not True:
            raise _Fail(_said(f"删除 {code}", status, payload))
        self.made.remove(code)

    def sweep(self) -> str:
        notes = []
        for code in reversed(list(self.made)):
            try:
                status, _payload = self.send("delete", code)
            except (OSError, RuntimeError, ValueError) as exc:
                notes.append(f"补偿删除 {code} 出错：{str(exc)[:60]}")
                continue
            self.made.remove(code)
            notes.append(f"已补偿删除 {code}" if status in (200, 404) else f"补偿删除 {code} 返回 {status}，留给快照还原")
        return "；".join(notes)


def _said(what: str, status: int, payload: dict) -> str:
    return f"{what} {status} {payload.get('code')} {payload.get('message')}"[:200]


# ---- 货位：新增下级后上级的末级标志----


def _position(run: _Run) -> str:
    picked = run.parts(pos_pick_sql(), 3)
    if picked is None:
        return "跳过下级货位核对：没有启用货位的仓库，或编码方案没有第二级"
    wh, parent, child = (safe_part(part) for part in picked)
    run.create(parent, {"name": _NAME + " 上级货位", "warehouse_code": wh})
    alone = run.cell(pos_flags_sql(parent, child))
    run.create(child, {"name": _NAME + " 下级货位", "warehouse_code": wh})
    nested = run.cell(pos_flags_sql(parent, child))
    run.delete(child)
    back = run.cell(pos_flags_sql(parent, child))
    run.delete(parent)
    gone = run.cell(pos_flags_sql(parent, child))
    # 两条都删掉之后再判，断言失败也不留档案。
    checks = (
        (alone, "1|-", "新建的一级货位 bPosEnd 应为 1"),
        (nested, "0|1", "新增下级后上级 bPosEnd 应由 U8 改成 0、下级为 1"),
        (back, "1|-", "删除下级后上级 bPosEnd 应恢复为 1"),
        (gone, "-|-", "删除后上下级货位都不应留下"),
    )
    for got, want, what in checks:
        if got != want:
            raise _Fail(f"{what}：回读（上级|下级）{got or '空'}，期望 {want}")
    return f"货位 {parent} 下新增 {child}：上级 bPosEnd 1→0，删除下级后恢复为 1"


# ---- 采购类型、销售类型：默认标志----


def _defaults(run: _Run) -> list[str]:
    cell = run.cell(defaults_sql(run.archive))
    return [safe_part(code) for code in cell.split(",")] if cell else []


def _restore(run: _Run, code: str, orig: list[str]) -> str:
    """把原来的默认类型改回 1、新类型改回 0；返回恢复中的问题（空串为成功）。"""
    for other in orig:
        if other not in _defaults(run):
            status, payload = run.update(other, {"bdefau": 1})
            if status != 200:
                return _said(f"把 {other} 改回默认", status, payload)
    if code in _defaults(run):
        status, payload = run.update(code, {"bdefau": 0})
        if status != 200:
            return _said(f"把 {code} 改回非默认", status, payload)
    return ""


def _type_default(run: _Run) -> str:
    picked = run.parts(arc_more_pick_sql(run.archive), 2)
    if picked is None:
        return "跳过默认标志核对：挑不到空闲编码或末级收发类别"
    code, rd = safe_part(picked[0]), safe_part(picked[1])
    orig = _defaults(run)
    run.create(code, {"name": _NAME + " 默认", "rstype_code": rd})
    status, payload = run.update(code, {"bdefau": 1})
    after = _defaults(run) if status == 200 else []
    restore = _restore(run, code, orig)
    run.delete(code)
    final = _defaults(run)
    if restore or final != orig:
        raise _Fail(f"默认类型没有恢复（{restore or '回读 ' + ','.join(final)}），原为 {','.join(orig) or '无'}")
    return _judge_default(code, orig, after, (status, payload))


def _judge_default(code: str, orig: list[str], after: list[str], answer: tuple[int, dict]) -> str:
    status, payload = answer
    if status != 200:
        raise _Fail(_said(f"把 {code} 的 bdefau 改成 1", status, payload))
    if code not in after:
        raise _Fail(f"bdefau 改成 1 后 {code} 的 bDefault 不是 1（默认类型 {','.join(after) or '无'}）")
    others = [other for other in after if other != code]
    if others:
        raise _Fail(f"bdefau 置 1 后 U8 没有清掉其他类型的默认标志：{','.join(others)}")
    if not orig:
        return f"{code} 设为默认成功；原来没有默认类型，无从核对清除"
    return f"{code} 设为默认后 U8 清掉了原默认 {','.join(orig)}；已恢复并删除 {code}"


# ---- 自定义项档案：使用长度按字符还是字节----


def _cjk_value(length: int) -> str:
    # 末 10 个字是时间（月日时分秒）换成汉字数字，前面用同一个字补足到 length 个字。
    stamp = dt.datetime.now().strftime("%m%d%H%M%S")
    return safe_cjk(_FILL * (length - len(stamp)) + "".join(_DIGITS[int(ch)] for ch in stamp))


def _define(run: _Run) -> str:
    picked = run.parts(define_pick_sql(), 2)
    if picked is None or not picked[1].isdigit():
        return "跳过汉字长度核对：没有使用长度 12 到 200 的可建档自定义项"
    cid, length = safe_part(picked[0]), int(picked[1])
    value = _cjk_value(length)
    status, payload = run.send("create", f"{cid}:{value}{_FILL}", {"alias": "ZC"})
    if status == 200:
        run.made.append(f"{cid}:{value}{_FILL}")
        raise _Fail(f"超过使用长度 {length} 个字的档案值应 400，实际新增成功")
    if (status, payload.get("code")) != (400, "bad_request"):
        raise _Fail(_said(f"超长 {length + 1} 个字的档案值应 400", status, payload))
    status, payload = run.send("create", f"{cid}:{value}", {"alias": "ZC"})
    if status != 200:
        raise _Fail(_said(f"自定义项 {cid} 使用长度 {length}：{length} 个汉字（{length * 2} 字节）被拒绝，U8 可能按字节计", status, payload))
    run.made.append(f"{cid}:{value}")
    stored = run.cell(define_count_sql(cid, value))
    run.delete(f"{cid}:{value}")
    gone = run.cell(define_count_sql(cid, value))
    if stored != "1|1":
        raise _Fail(f"{length} 个汉字的档案值新增后回读（完整|同前缀）{stored or '空'}，期望 1|1（U8 可能按字节截断）")
    if gone != "0|0":
        raise _Fail(f"删除后回读 {gone or '空'}，期望 0|0")
    return f"自定义项 {cid} 使用长度 {length}：{length} 个汉字完整写入，多一个字 400（按字符计）"


_PROBES = {
    "position": _position,
    "purchase_type": _type_default,
    "sale_type": _type_default,
    "user_define": _define,
}
PROBED = tuple(_PROBES)


def arc_probe(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    archive = dict(step.fields)["archive"]
    probe = _PROBES.get(archive)
    if probe is None:
        return "FAIL", "没有该档案的附加核对：" + archive
    run = _Run(env, spec, archive)
    try:
        return "PASS", probe(run)
    except (_Fail, OSError, RuntimeError, ValueError) as exc:
        swept = run.sweep()
        return "FAIL", ((swept + "；") if swept else "") + str(exc)[:200]
    finally:
        if run.made:
            run.sweep()


ARC_PROBE_OPS = {"arc_probe": arc_probe}


def probe_step(archive: str) -> Step:
    return Step("custom", fields=(("archive", archive),), sql_name="arc_probe", id_from="none")
