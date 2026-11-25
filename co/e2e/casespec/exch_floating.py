"""浮动汇率账套里新增一天的汇率，U8 存的 exch.cdate 是什么写法（yyyy-mm-dd、日数还是两位日数）。

archive-exchange-rate-floating：账套汇率方式是固定汇率时（AccInformation AA / iXchgRateStl 为 True / 1 / 空），用例里临时
  改成浮动（True→False、1→0），跑完按原值改回；已经是浮动汇率时不动选项。挑编码最小的外币，在登录年度登录月份之后找一个
  该币种还没有浮动汇率、总账未结账的期间，日取该期间的 5 号（5、05、yyyy-mm-dd 三种写法互不相同），依次：
  列表的 rate_mode 是 floating → 新增 <币种>:<年>:<期>:<yyyy-mm-dd>（EAI）→ SQL 回读恰好一行 itype=1、汇率一致，记下 cdate
  原文并判定写法 → 响应 code 的日段等于 cdate 原文（桥按实际 cdate 改写）→ 按日期读取 <币种>:<yyyy-mm-dd> 200、汇率一致、
  mode floating、day 等于 cdate → 删除 → SQL 回读没有行。判定里写明 cdate 的写法，供 api-reference §15 收口。
恢复：先尽力删掉本用例的汇率（404 视为已删），再改回选项并回读核对。
挑不到外币、空期间（登录月份是 12 月等）、选项行不是恰好一行记 N/A。改的是账套级选项，独占。
步骤 kind 为 "custom"，sql_name 是操作名（EXCH_FLOATING_OPS 经 flip 并入 ops_run）。
"""

from __future__ import annotations

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.exch_floating_sql import floating_rows_sql, foreign_sql, mode_set_sql, mode_sql, periods_sql
from co.e2e.casespec.flip_util import Fail, brief, ok, row, rowcount, rows, same_num, send, with_restore

ARCHIVE = "exchange_rate"
ROUTES = tuple("/v1/archives/" + op for op in ("create", "get", "update", "delete", "list"))
RATE = 6.5432
DAY = 5


def is_fixed(value: str) -> bool:
    """同桥 ArcExch.Mode：空、1、true（不分大小写）为固定汇率。"""
    text = value.strip()
    return text in ("", "1") or text.lower() == "true"


def floating_value(old: str) -> str:
    return "0" if old.strip() in ("0", "1") else "False"


def cdate_form(cdate: str, day: str) -> str:
    if cdate == day:
        return "yyyy-mm-dd"
    if cdate == str(int(day[8:10])):
        return "日数"
    if cdate == day[8:10]:
        return "两位日数"
    return ""


def free_period(cells: list[list[str]], month: int) -> int:
    """periods_sql 的结果：登录月份之后、没有该币种浮动汇率、总账未结账的最大期间；没有为 0。"""
    free = [int(c[0]) for c in cells if len(c) == 3 and c[0].isdigit() and c[1] == "0" and c[2] == "0"]
    return max((p for p in free if month < p <= 12), default=0)


class _Run:
    def __init__(self, env: Env, spec: CaseSpec, currency: str, period: int) -> None:
        self.env = env
        self.spec = spec
        self.currency = currency
        self.year = int(env.today[:4])
        self.period = period
        self.day = f"{self.year}-{period:02d}-{DAY:02d}"
        self.code = f"{currency}:{self.year}:{period}:{self.day}"

    def call(self, op: str, body: dict, what: str) -> dict:
        return ok(self.env, "/v1/archives/" + op, dict(body, archive=ARCHIVE), what)

    def floating_rows(self) -> list[list[str]]:
        return rows(self.env, self.spec, floating_rows_sql(self.year, self.period))

    def cleanup(self) -> str:
        if not self.floating_rows():
            return ""
        status, payload = send(self.env, "/v1/archives/delete", {"archive": ARCHIVE, "code": self.code})
        if status not in (200, 404) or self.floating_rows():
            return "删不掉本用例的浮动汇率：" + brief(status, payload)
        return ""


def _stored(run: _Run) -> str:
    """新增之后的回读：恰好一行、汇率一致，返回 cdate 原文。"""
    found = run.floating_rows()
    if len(found) != 1 or len(found[0]) != 3 or not same_num(found[0][2], RATE):
        raise Fail(f"新增后 {run.currency} {run.year}-{run.period} 的浮动汇率行应恰好一行、汇率 {RATE}，实际 {found}")
    return found[0][1]


def _check_get(run: _Run, cdate: str) -> None:
    payload = run.call("get", {"code": f"{run.currency}:{run.day}"}, "按日期读取")
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    if not same_num(fields.get("rate"), RATE) or fields.get("mode") != "floating" or fields.get("day") != cdate:
        raise Fail(f"按日期读取的 rate、mode、day 应为 {RATE}、floating、{cdate}，实际 {fields}"[:240])
    if payload.get("rate_mode") != "floating":
        raise Fail(f"按日期读取的 rate_mode 应为 floating，实际 {payload.get('rate_mode')}")


def _steps(run: _Run) -> tuple[str, str]:
    listed = run.call("list", {"currency": run.currency, "fiscal_year": run.year, "limit": 1}, "列出汇率")
    if listed.get("rate_mode") != "floating":
        raise Fail(f"改成浮动汇率之后列表的 rate_mode 应为 floating，实际 {listed.get('rate_mode')}")
    created = run.call("create", {"code": run.code, "fields": {"rate": RATE}}, "新增浮动汇率")
    cdate = _stored(run)
    form = cdate_form(cdate, run.day)
    if not form:
        raise Fail(f"U8 存的 cdate 是 {cdate!r}，不是 {run.day}、{DAY}、{DAY:02d} 三种写法之一")
    want = f"{run.currency}:{run.year}:{run.period}:{cdate}"
    if created.get("code") != want:
        raise Fail(f"新增响应的 code 应按实际 cdate 改写成 {want!r}，实际 {created.get('code')!r}")
    _check_get(run, cdate)
    deleted = run.call("delete", {"code": want}, "删除浮动汇率")
    if deleted.get("deleted") is not True or run.floating_rows():
        raise Fail("删除之后仍有浮动汇率行，或响应缺 deleted")
    return "PASS", f"浮动汇率 {run.day}：U8 存的 cdate 是 {cdate!r}（{form}），新增、按日期读取、删除都对"


def _restore(env: Env, spec: CaseSpec, old: str, new: str) -> str:
    done = rowcount(env, spec, mode_set_sql(old, new))
    got = row(env, spec, mode_sql())
    if got != ["1", old]:
        return f"汇率方式选项应改回 {old or '空'}，实际 {got}（改动 {done} 行）"
    return ""


def _pick(env: Env, spec: CaseSpec) -> tuple[str, str, int, str]:
    """(N/A 原因, 币种, 期间, 选项原值)；原因为空表示可以跑。"""
    mode = row(env, spec, mode_sql())
    if len(mode) != 2 or mode[0] != "1":
        return "账套选项 AA / iXchgRateStl 不是恰好一行，不动选项", "", 0, ""
    found = row(env, spec, foreign_sql())
    currency = found[0] if found else ""
    if not currency:
        return "测试账套里没有外币", "", 0, ""
    year, month = int(env.today[:4]), int(env.today[5:7])
    period = free_period(rows(env, spec, periods_sql(year)), month)
    if not period:
        return f"{currency} 在 {year} 年登录月份之后没有空的、未结账的期间", currency, 0, ""
    return "", currency, period, mode[1]


def exch_floating(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    reason, currency, period, old = _pick(env, spec)
    if reason:
        return "N/A", reason
    run = _Run(env, spec, currency, period)
    new = floating_value(old) if is_fixed(old) else old

    def body() -> tuple[str, str]:
        if new != old and rowcount(env, spec, mode_set_sql(new, old)) != 1:
            raise Fail(f"临时把汇率方式从 {old or '空'} 改成 {new} 应改 1 行")
        status, detail = _steps(run)
        return status, detail + (f"（选项 {old or '空'}→{new} 临时改成浮动）" if new != old else "（账套本来就是浮动汇率）")

    undos = [("清理浮动汇率", run.cleanup)]
    if new != old:
        undos.append(("恢复汇率方式选项", lambda: _restore(env, spec, old, new)))
    return with_restore(body, tuple(undos))


EXCH_FLOATING_OPS = {"exch_floating": exch_floating}


def exch_floating_cases() -> list[CaseSpec]:
    # 改账套级汇率方式：单据、汇率读取、外币核销都读它，独占；同时带汇率档案的写标签。
    tags = frozenset(("arc:" + ARCHIVE, "arc:exchange"))
    step = Step("custom", sql_name="exch_floating", id_from="none")
    title = "浮动汇率：临时改成浮动汇率方式，新增一天的汇率，回读 U8 存的 cdate 写法，按日期读取、删除，选项原样恢复"
    return [CaseSpec("archive-exchange-rate-floating", title, (), (step,), True, tags=tags, routes=ROUTES, exclusive=True)]
