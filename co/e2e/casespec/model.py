"""一条用例：依赖的挑单、若干桥调用或 SQL、以及对响应的断言。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    kind: str
    route: str = ""
    fields: tuple[tuple[str, str], ...] = ()
    status: int = 200
    code: str = ""
    checks: tuple[tuple[str, str], ...] = ()
    sql_name: str = ""
    id_from: str = "need"


@dataclass(frozen=True)
class CaseSpec:
    name: str
    title: str
    needs: tuple[str, ...] = ()
    steps: tuple[Step, ...] = ()
    requires_login: bool = False
    # 测试账套里可能根本没有这类单据（例如流程停用的来料不良品处理单）。缺单据记 N/A，不算失败。
    optional: bool = False
    # 需要的测试数据键（co.e2e.testdata）。缺一个就记 SKIP，一步也不跑。
    data: tuple[str, ...] = ()
    # 并行调度用的资源标签（自动推导之外另加的，见 casespec/tags.py）。标签相交的两条用例不同时跑。
    tags: frozenset[str] = frozenset()
    # 步骤里看不出 /v1 路径时（core、custom 等自定义步骤）手写本用例用到的路由，供标签推导和按改动选用例。
    routes: tuple[str, ...] = ()
    # 独占：跑的时候不和任何用例同时跑（例如自己就发一批并发请求的 bridge-concurrency）。
    exclusive: bool = False
    # 观察型用例：PASS 时报告的说明 = 标题 + 各步骤 PASS 的说明（实测记下的值），不只是标题。缺省关闭。
    observe: bool = False


def call(route: str, fields: tuple[tuple[str, str], ...], checks: tuple = (), status: int = 200, code: str = "") -> Step:
    return Step("call", route, fields, status, code, checks)


def call_made(route: str, fields: tuple[tuple[str, str], ...], checks: tuple = (), status: int = 200, code: str = "") -> Step:
    return Step("call", route, fields, status, code, checks, "", "made")


def read_step(sql_name: str, mode: str, id_from: str = "need") -> Step:
    return Step("read", "", (), 200, mode, (), sql_name, id_from)


def sql_step(sql_name: str, want: str, id_from: str = "need") -> Step:
    return Step("sql", "", (), 200, want, (), sql_name, id_from)


def _expect(expect: tuple) -> tuple[int, str]:
    if len(expect) == 2:
        return int(expect[0]), str(expect[1])
    return 200, ""


def apply_step(route: str, builder: str, id_from: str = "made", checks: tuple = (), expect: tuple = ()) -> Step:
    status, code = _expect(expect)
    return Step("apply", route, (), status, code, checks, builder, id_from)


def call_from(route: str, fields: tuple, id_from: str, checks: tuple = (), expect: tuple = ()) -> Step:
    status, code = _expect(expect)
    return Step("call", route, fields, status, code, checks, "", id_from)


def pred_step(sql_name: str, pred: str, id_from: str = "made") -> Step:
    return Step("pred", "", (), 200, pred, (), sql_name, id_from)


def stash_step(sql_name: str, key: str, id_from: str = "made") -> Step:
    return Step("stash", "", (), 200, key, (), sql_name, id_from)


def keep_step(key: str, id_from: str = "made") -> Step:
    return Step("keep", "", (), 200, key, (), "", id_from)
