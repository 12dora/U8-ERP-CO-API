"""按登记表跑用例。缺单据记 SKIP，不把跳过当成通过。

jobs 为 1（--serial）时一条接一条跑，与以前完全一样。jobs 大于 1 时交给 sched：登录及其之前串行，
其余资源不冲突的同时跑；每条用例用自己的一份 Env（bag 复制、made/focus/last_code/sql_n 从头算），
结果跑完后按登记顺序写进报告。
"""

from __future__ import annotations

import sys
import threading
from dataclasses import replace

from co.client.u8co_client import U8CoError
from co.e2e import testdata, timing
from co.e2e.settings import ConfigError, conf
from co.e2e.wpolicy import lane_blocked
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arc_write import CORE_HANDLERS
from co.e2e.casespec.arap_gen_run import step_core
from co.e2e.casespec.ops_run import step_custom
from co.e2e.casespec.bom_write import BOMW_CLEANUP
from co.e2e.casespec.mo_create import MOC_CLEANUP
from co.e2e.casespec.so_lock import LOCK_CLEANUP
from co.e2e.casespec.qm_write import QMI_CLEANUP
from co.e2e.casespec.pu_red_chain import REDPUR_CLEANUP
from co.e2e.casespec.sale_red_invoice import RS_CLEANUP
from co.e2e.casespec.stock_other_docs import SM_CLEANUP
from co.e2e.casespec.transfer_gen import TG_CLEANUP
from co.e2e.casespec.mo_update import MOU_CLEANUP
from co.e2e.casespec.arap_verify import ARV_CLEANUP
from co.e2e.casespec.arap_voucher import AV_CLEANUP
from co.e2e.casespec.writeoff import HX_CLEANUP
from co.e2e.casespec.writeoff_auto import HXA_CLEANUP
from co.e2e.casespec.dry_suite import DRY_SUITE_CLEANUP
from co.e2e.casespec.qm_update import QMU_CLEANUP
from co.e2e.casespec.picks import fill_bag, pick_failed
from co.e2e.casespec.gate import GatedClient, stopped
from co.e2e.casespec.sched import CaseCrashed, Interrupted, Rules, crash_text, default_rules, run_waves, split_wave0
from co.e2e.casespec.select import filter_cases, parse_names
from co.e2e.casespec.catalog import build_cases
from co.e2e.casespec.steps import step_apply, step_call, step_call_foreign, step_create, step_keep
from co.e2e.casespec.steps_sql import (
    step_acc_foreign,
    step_audit,
    step_badsig,
    step_health,
    step_login,
    step_pred,
    step_read,
    step_readcode,
    step_sql,
    step_stash,
)

SPARSE_NA = "本通道账套没有这类单据（sparse_samples）"

# 用例中途失败时的补偿：用例名 → 补偿函数。
_CLEANUP = {**MOC_CLEANUP, **BOMW_CLEANUP, **QMI_CLEANUP, **RS_CLEANUP, **REDPUR_CLEANUP, **LOCK_CLEANUP}
_CLEANUP.update(SM_CLEANUP)  # 形态转换单、调拨申请单、盘点单
_CLEANUP.update(TG_CLEANUP)  # 调拨单参照调拨申请单
_CLEANUP.update(MOU_CLEANUP)  # 生产订单修改
_CLEANUP.update(ARV_CLEANUP)  # 应付审核 / 应收审核：失败时尽力弃审、弃复
_CLEANUP.update(HX_CLEANUP)  # 核销：失败时补偿取消核销
_CLEANUP.update(HXA_CLEANUP)  # 自动核销：失败时补偿取消核销
_CLEANUP.update(AV_CLEANUP)  # 应收制单：失败时尽力取消制单、弃审、弃复
_CLEANUP.update(DRY_SUITE_CLEANUP)  # 为预演真建的单据、真做的核销和制单，失败时尽力撤回
_CLEANUP.update(QMU_CLEANUP)  # 检验单修改：倒序删除（提交了审批的先撤回）
_HANDLERS = {
    "health": step_health,
    "badsig": step_badsig,
    "accforeign": step_acc_foreign,
    "login": step_login,
    "audit": step_audit,
    "call": step_call,
    "callforeign": step_call_foreign,
    "create": step_create,
    "apply": step_apply,
    "keep": step_keep,
    "stash": step_stash,
    "pred": step_pred,
    "sql": step_sql,
    "read": step_read,
    "readcode": step_readcode,
    "core": step_core,
}
# 总账和档案的步骤（gl_voucher / arc_write）。
_HANDLERS.update(CORE_HANDLERS)
# 自定义步骤（ops_run）。
_HANDLERS["custom"] = step_custom


def _gap(spec: CaseSpec, bag: dict[str, str]) -> str:
    for key in spec.needs:
        if not bag.get(key):
            return key
    return ""


def _sparse_lane() -> bool:
    try:
        return conf().sparse_samples
    except ConfigError:
        return False


def _no_sample(spec: CaseSpec, bag: dict[str, str]) -> tuple[str, str]:
    # 必需挑单挑不到。可选用例记 N/A；小账套通道（account.sparse_samples）上，只要缺的键都是查询正常但没挑到，
    # 也记 N/A。挑单 SQL 出错的照旧 SKIP（报告判 FAIL）。
    if spec.optional:
        return "N/A", "账套没有这类单据（可选用例）"
    missing = {key for key in spec.needs if not bag.get(key)}
    if _sparse_lane() and not (missing & pick_failed(bag)):
        return "N/A", SPARSE_NA
    return "SKIP", "没有符合条件的单据"


def run_step(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    handler = _HANDLERS.get(step.kind)
    if handler is None:
        return "FAIL", "未知步骤"
    return handler(env, spec, step)


# 观察型用例 PASS 说明的长度上限（失败说明仍是 240）。
OBSERVE_MAX = 600


def walk(env: Env, spec: CaseSpec) -> tuple[str, str]:
    observed: list[str] = []
    for index, step in enumerate(spec.steps):
        if stopped(env.ctx):
            return "SKIP", f"步骤{index + 1} 收到中断，没有执行"
        status, detail = run_step(env, spec, step)
        if status != "PASS":
            return status, f"步骤{index + 1} {detail}"[:240]
        if spec.observe and detail and detail != spec.title:
            observed.append(detail)
    if observed:
        return "PASS", "；".join([spec.title, *observed])[:OBSERVE_MAX]
    return "PASS", spec.title


def judge_one(env: Env, spec: CaseSpec) -> tuple[str, str]:
    # 写入策略没能还原时本通道之后的用例不再跑（受限策略下只会得到一串 403/429/503）。
    lane_block = lane_blocked()
    if lane_block:
        return "SKIP", lane_block[:240]
    if spec.requires_login and env.bag.get("login") != "1":
        return "SKIP", "登录未成功"
    missing = testdata.first_missing(spec.data)
    if missing:
        return "SKIP", testdata.DataMissing(missing).reason()
    if _gap(spec, env.bag):
        return _no_sample(spec, env.bag)
    status, detail = walk(env, spec)
    cleanup = _CLEANUP.get(spec.name)
    if status != "PASS" and cleanup is not None:
        # 生产订单、物料清单用例中途失败时尽力弃审并删掉本用例建的单（快照还原前也不留数据）。
        detail = (cleanup(env, spec.name) + "；" + detail)[:240]
    return status, detail


def _extra(spec: CaseSpec, took: int | None) -> dict:
    # 报告按用例名前缀汇总耗时（timing.by_family）。
    return {"case": spec.name, "duration_ms": took}


def run_one(env: Env, spec: CaseSpec) -> None:
    started = timing.monotonic()
    status, detail = judge_one(env, spec)
    env.ctx.add_test(spec.title, status, detail, _extra(spec, timing.elapsed_ms(started)))


def env_for(base: Env) -> Env:
    """并行时每条用例一份 Env：bag 复制一份（本用例写的键不影响别人），made、focus、last_code、sql_n 从头算。
    客户端、操作员、日期、年度、姓名和 ctx 共用（客户端每次调用新开连接，线程安全）。"""
    return replace(base, bag=dict(base.bag), made={}, focus="", last_code="", sql_n=0)


def _as_int(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    return None


def _put_doc(env: Env, key: str, attr: str) -> None:
    if key in env.bag:
        setattr(env.ctx, attr, _as_int(env.bag[key]))


def publish(env: Env) -> None:
    _put_doc(env, "sale_id", "sale_id")
    _put_doc(env, "dispatch_id", "dispatch_id")
    _put_doc(env, "dispatch_downstream", "dispatch_downstream_id")
    mode = env.bag.get("dispatch_mode") or ""
    if mode:
        env.ctx.dispatch_mode = mode


def run_selected(env: Env, text: str, jobs: int = 1) -> None:
    names = parse_names(text)
    env.bag = fill_bag(env, names)
    publish(env)
    specs = filter_cases(build_cases(env.bag), names)
    if jobs <= 1:
        for spec in specs:
            run_one(env, spec)
        return
    run_concurrent(env, specs, jobs)


def _emit(env: Env, specs: list[CaseSpec], results: dict[str, tuple[str, str]], took: dict[str, int]) -> None:
    by_name = {spec.name: spec for spec in specs}
    for name, (status, detail) in results.items():
        spec = by_name[name]
        env.ctx.add_test(spec.title, status, detail, _extra(spec, took.get(name)))


def _pool(env: Env, action: str) -> None:
    # 假的 remote（单测）没有通道池，照旧用 sql_channel。
    method = getattr(env.ctx.remote, action, None)
    if method is not None:
        method()


def _stop_flag(env: Env) -> threading.Event:
    flag = getattr(env.ctx, "stop", None)
    return flag if isinstance(flag, threading.Event) else threading.Event()


def cap_jobs(env: Env, jobs: int) -> int:
    """桥 /health 报的写工人数（workers）小于 jobs 时按它来；取不到就不动。多开只会在桥上排队到超时。"""
    try:
        payload = env.client.health()
    except (U8CoError, OSError, ValueError):
        return jobs
    workers = payload.get("workers") if isinstance(payload, dict) else None
    if type(workers) is int and 1 <= workers < jobs:
        print(f"桥只有 {workers} 个写工人，并行度从 {jobs} 降到 {workers}", file=sys.stderr)
        return workers
    return jobs


def _record(env: Env, exc: Interrupted | CaseCrashed) -> None:
    for name, error in exc.crashes:
        env.ctx.fail(crash_text(name, error))
    stuck = getattr(exc, "stuck", [])
    if stuck:
        env.ctx.fail("中断后这些用例没在时限内停下（已禁止再发桥调用和 SQL）：" + ",".join(stuck))


def run_concurrent(env: Env, specs: list[CaseSpec], jobs: int) -> None:
    """第 0 波（登录及其之前）用 base 这一份，登录结果留在它上面；之后每条用例一份 env_for。
    桥客户端包上中断闸门：Run.stop 置位后谁都发不出新的桥调用。"""
    stop = _stop_flag(env)
    base = replace(env, client=GatedClient(env.client, stop))
    jobs = cap_jobs(base, jobs)
    if hasattr(env.ctx, "jobs"):
        env.ctx.jobs = jobs
    head = {spec.name for spec in specs[: split_wave0(specs)]}
    # 每条用例自己的耗时（含在桥上排队的时间）；各线程只写自己的键。
    took: dict[str, int] = {}

    def one(spec: CaseSpec) -> tuple[str, str]:
        started = timing.monotonic()
        try:
            return judge_one(base if spec.name in head else env_for(base), spec)
        finally:
            took[spec.name] = timing.elapsed_ms(started)

    conflicts, bind, exclusive = default_rules()
    _pool(env, "open_sql_pool")
    try:
        results = run_waves(specs, one, jobs, Rules(conflicts, bind, env.bag, exclusive, stop))
    except (Interrupted, CaseCrashed) as exc:
        # 已跑完的照样进报告（登记顺序），每条异常各记一条失败，再交给 engine：它负责记失败和还原。
        _emit(env, specs, exc.results, took)
        _record(env, exc)
        raise
    finally:
        _pool(env, "close_sql_pool")
    _emit(env, specs, results, took)
