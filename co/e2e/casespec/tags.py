"""用例的资源标签：并行调度时，两条用例的标签相交（或任一方独占）就不同时跑。纯函数，不连服务器。

标签写成 family:rest。自动推导（auto_tags）看 needs 和步骤；步骤里看不出来的，在用例登记处的 CaseSpec 上
手写 tags= / exclusive=（例如并发用例、自定义步骤、只在构造器里才知道单据类型的生单）。
登记表（挑单键的单据类型、动现存量的类型、单据链等）在 tags_data.py，新用例往那里追加；这里照旧可以 import。

词表：
- need:<挑单键>        spec.needs 每一项。同键 = 同一张单据（挑单一轮只挑一次），彼此互斥。
- doc:<类型>:<id>      bind_tags 按挑单结果补上；两个不同的键挑中同一张单据时靠它互斥。need:<键> 保留。
- src:<类型>:<键>      生单 / 改单回写的来源单据；bind_tags 换成 src:<类型>:<id>。
- new:<类型>           新建或生单（单据号、VoucherHistory 不随快照回退）。同桥 DocLocks 的交叉锁：
                       发货单 ↔ 退货单、到货单 ↔ 采购退货单互相带上对方的 new:。
- new:gl:<凭证类别>    总账新建凭证（按凭证类别编号）。
- w:<类型>             对该类型的写（审核、修改、关闭、删除）。只是标记：写与写不因此互斥，只让 list:<类型> 避开。
- chain:<链>           sale / pu / mfg 单据链上的写（按 CHAIN_OF 由 new: / w: 自动推导，probe 用例不带），彼此互斥：
                       下游生单、改单会回写上游订单行，挑单拿不到上游订单 id，只好按链粗粒度互斥。
- stock                动现存量（CurrentStock / InvPosition / 预留）的写，彼此互斥。
- stock-read           断言现存量的读，只和 stock 冲突。
- gl                   总账凭证写，彼此互斥。
- opening:pu           采购期初记账（GL_mend 的 bflag_PU），彼此互斥；同时带 chain:pu。
- period:close         月末结账（GL_mend 第 1 到 12 期的结账标志），彼此互斥；同时带 opening:pu（同一张表）。
- ia:post              存货核算记账、期末处理（改出入库单据行的记账人和 IA_* 表、读 GL_mend），彼此互斥；
                       同时带 period:close、opening:pu。这类用例还写 exclusive=True（整个存货核算过程持全局写闸门）。
- wf                   审批流写动作（桥的孤儿任务清理是进程级闸门），彼此互斥。state / history / tasks 不带。
- arc:<档案>           档案写（含挑空闲编码、删除闸门），彼此互斥。arc-read:<档案> 只读，只和 arc:<档案> 冲突。
- list:<类型>          列表断言，只和 new:<类型>、w:<类型> 冲突。
- rpt:<域>             报表断言，只和该域的写冲突（见 RPT_HITS）。报表同时带 wave:2。
- wave:2               调度约定：等所有不带 wave:2 的用例跑完再开始（sched 还没实现时，rpt:* 的冲突规则兜底）。
- audit                拉审计日志（固定文件名 audit-<日>.log），彼此互斥。
- bom                  改物料清单版本（含来源版本失效日期）与按 BOM 展开新建生产订单，彼此互斥。
- burst                bridge-concurrency 自己就发 12 个并发请求；CaseSpec 上同时写 exclusive=True。
- login                登录用例本身（屏障由 sched 第 0 波保证，不当互斥）。
- meta                 只走桥的 HTTP 线程（健康检查、错误签名、名单外账套、字段元数据），不和任何用例冲突。
- opt:<选项>           依赖账套选项（只读，e2e 从不改选项），不参与冲突。
- probe                只探参数校验（桥在进 U8 之前就拒绝），不参与冲突。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.tags_data import (
    ARC_LINKS,
    CHAIN_OF,
    CROSS_NEW,
    EXCLUSIVE_TAGS,
    FIXED_ROUTES,
    GL_READS,
    GL_SIGNS,
    KINDS,
    MARK_PREFIXES,
    MARKS,
    NEED_KIND,
    CORE_LISTS,
    CUSTOM_FIXED,
    REPORT_DOMAIN,
    RPT_HITS,
    SIMPLE_STEPS,
    SOURCE_NEEDS,
    STOCK_KINDS,
    VOUCHER,
    WF_WRITES,
    WRITE_OPS,
)

__all__ = [
    "ARC_LINKS",
    "CHAIN_OF",
    "CROSS_NEW",
    "EXCLUSIVE_TAGS",
    "KINDS",
    "NEED_KIND",
    "REPORT_DOMAIN",
    "RPT_HITS",
    "SOURCE_NEEDS",
    "STOCK_KINDS",
    "auto_tags",
    "bind_tags",
    "conflicts",
    "is_exclusive",
    "is_mark",
    "need_kind",
    "step_kind",
]


def need_kind(key: str) -> str:
    if key in NEED_KIND:
        return NEED_KIND[key]
    if key.startswith("load_"):
        return key[len("load_") :]
    return ""


def _new(kind: str) -> set[str]:
    found = {"new:" + kind}
    found.update("new:" + other for other in CROSS_NEW.get(kind, ()))
    return found


def step_kind(step: Step) -> str:
    """步骤操作的单据类型：fields 的 type，其次断言里的 type，再次固定路由。"""
    kind = dict(step.fields).get("type", "")
    if kind:
        return kind
    kind = dict(step.checks).get("type", "")
    if kind:
        return kind
    return FIXED_ROUTES.get(step.route, "")


def _voucher_op(route: str) -> str:
    if route in FIXED_ROUTES:
        return "verify"
    if route.startswith(VOUCHER):
        return route[len(VOUCHER) :]
    return ""


def _made(step: Step, needs: tuple[str, ...]) -> bool:
    # id 来自本用例新建的单据：made，或 keep 记进 bag 的键（bag:<键>，但挑单键不算）。
    if step.id_from == "made":
        return True
    if not step.id_from.startswith("bag:"):
        return False
    key = step.id_from[len("bag:") :]
    return key not in needs and key not in NEED_KIND


def _voucher_tags(step: Step, needs: tuple[str, ...]) -> set[str]:
    kind = step_kind(step)
    op = _voucher_op(step.route)
    if not kind or not op:
        return set()
    found: set[str] = set()
    if op in ("create", "generate") or _made(step, needs):
        found.update(_new(kind))
    if op in WRITE_OPS:
        found.add("w:" + kind)
    return found


def _call_tags(step: Step, needs: tuple[str, ...]) -> set[str]:
    if step.route.startswith("/v1/workflow/"):
        return {"wf"} if step.route.rsplit("/", 1)[-1] in WF_WRITES else set()
    return _voucher_tags(step, needs)


def _create_tags(step: Step) -> set[str]:
    kind = step_kind(step) or (step.sql_name if step.sql_name in KINDS else "")
    return _new(kind) if kind else set()


def _gl_tags(step: Step) -> set[str]:
    op = step.route.rsplit("/", 1)[-1]
    if op in GL_READS:
        return set()
    found = {"gl"}
    sign = GL_SIGNS.get(step.sql_name)
    if op == "create" and sign:
        found.add("new:gl:" + sign)
    return found


def _arc_tags(names: Iterable[str], family: str) -> set[str]:
    found: set[str] = set()
    for name in names:
        if not name:
            continue
        found.add(family + name)
        found.update(family + other for other in ARC_LINKS.get(name, ()))
    if family == "arc:" and found:
        # 桥对全部档案写入持全局键 arc:write（EAI 档案写并发会卡住），这里同样互斥，免得在桥里排队超时。
        found.add("arc:write")
    return found


def _report_tags(name: str) -> set[str]:
    domains = REPORT_DOMAIN.get(name)
    if domains is None:
        return set()
    return {"wave:2"} | {"rpt:" + item for item in domains}


def _core_tags(step: Step) -> set[str]:
    if step.sql_name in CORE_LISTS:
        return {"list:" + step_kind(step)}
    if step.sql_name == "stock_filter":
        return {"stock-read"}
    if step.sql_name == "burst":
        return {"burst", "new:other_in", "stock"}
    return set()


def _custom_tags(step: Step, needs: tuple[str, ...]) -> set[str]:
    op = step.sql_name
    fields = dict(step.fields)
    if op in CUSTOM_FIXED:
        return set(CUSTOM_FIXED[op])
    if op == "report":
        return _report_tags(fields.get("name", ""))
    if op == "archive_read":
        return _arc_tags((fields.get("archive", ""),), "arc-read:")
    if op == "voucher_list":
        return {"list:" + fields.get("type", "")}
    if op in ("arc_more_cycle", "arc_more_guard", "arc_more_sub_ignored", "arc_more_alias_dup"):
        names = fields.get("archives", "position,unit").split(",") if op == "arc_more_guard" else ()
        return _arc_tags(names or (fields.get("archive", ""),), "arc:")
    if step.route.startswith(VOUCHER):
        # 带单据路由的自定义步骤（拒绝调用、无来源新建等）：按路由和 fields 的 type 推。
        return _voucher_tags(step, needs)
    return _report_tags(op)


def _step_tags(step: Step, needs: tuple[str, ...]) -> set[str]:
    if step.kind in SIMPLE_STEPS:
        return set(SIMPLE_STEPS[step.kind])
    if step.kind in ("call", "apply", "callforeign"):
        return _call_tags(step, needs)
    if step.kind == "create":
        return _create_tags(step)
    if step.kind == "core_gl":
        return _gl_tags(step)
    if step.kind in ("core_arc", "core_arcsql", "core_arcpick"):
        return _arc_tags((dict(step.fields).get("archive", ""),), "arc:")
    if step.kind == "core":
        return _core_tags(step)
    if step.kind == "custom":
        return _custom_tags(step, needs)
    return set()


def _derived(found: set[str]) -> set[str]:
    """由 new: / w: 推出的 stock 和 chain:<链>（手写的标签也算进来）。probe 用例不进 U8，不推。"""
    extra: set[str] = set()
    if "probe" in found:
        return extra
    for tag in found:
        family, _sep, kind = tag.partition(":")
        if family not in ("new", "w"):
            continue
        if kind in STOCK_KINDS:
            extra.add("stock")
        if kind in CHAIN_OF:
            extra.add("chain:" + CHAIN_OF[kind])
    return extra


def auto_tags(spec: CaseSpec) -> frozenset[str]:
    """spec.tags + needs + 各步骤推导出的标签（挑单前，不含 doc:）。"""
    found = set(spec.tags)
    for key in spec.needs:
        found.add("need:" + key)
        if key in SOURCE_NEEDS:
            found.add("src:" + need_kind(key) + ":" + key)
    for step in spec.steps:
        found.update(_step_tags(step, spec.needs))
    found.discard("list:")
    found.update(_derived(found))
    return frozenset(found)


def _bound_src(tag: str, bag: Mapping[str, str]) -> str:
    kind, _sep, key = tag[len("src:") :].partition(":")
    value = str(bag.get(key) or "")
    return "src:" + kind + ":" + value if value.isdigit() else tag


def bind_tags(spec: CaseSpec, bag: Mapping[str, str]) -> frozenset[str]:
    """挑单之后：挑中的单据补 doc:<类型>:<id>，src:<类型>:<键> 换成 id。need:<键> 保留（同键仍互斥）。"""
    found = {_bound_src(tag, bag) if tag.startswith("src:") else tag for tag in auto_tags(spec)}
    for key in spec.needs:
        kind = need_kind(key)
        value = str(bag.get(key) or "")
        if kind and value.isdigit():
            found.add("doc:" + kind + ":" + value)
    return frozenset(found)


def is_exclusive(spec: CaseSpec) -> bool:
    return spec.exclusive or bool(EXCLUSIVE_TAGS & set(spec.tags))


def is_mark(tag: str) -> bool:
    """只读或调度用的标记：两边都有也不互斥。"""
    return tag in MARKS or tag.startswith(MARK_PREFIXES)


def _matches(tag: str, pattern: str) -> bool:
    if pattern.endswith((":", "_")):
        return tag.startswith(pattern)
    return tag == pattern


def _hits_of(tag: str) -> tuple[str, ...]:
    if tag == "stock-read":
        return ("stock",)
    if tag.startswith("list:"):
        kind = tag[len("list:") :]
        return ("new:" + kind, "w:" + kind)
    if tag.startswith("arc-read:"):
        return ("arc:" + tag[len("arc-read:") :],)
    return RPT_HITS.get(tag, ())


def _reads_hit(readers: frozenset[str], writers: frozenset[str]) -> bool:
    for tag in readers:
        for pattern in _hits_of(tag):
            if any(_matches(other, pattern) for other in writers):
                return True
    return False


def conflicts(a: frozenset[str], b: frozenset[str], a_exclusive: bool = False, b_exclusive: bool = False) -> bool:
    """两条用例能否同时跑：任一独占、非标记标签相交、或一方的读标签撞上另一方的写，就算冲突。"""
    if a_exclusive or b_exclusive:
        return True
    if EXCLUSIVE_TAGS & (a | b):
        return True
    if any(not is_mark(tag) for tag in a & b):
        return True
    return _reads_hit(a, b) or _reads_hit(b, a)
