"""造完档案后写这条通道的测试数据文件（配置 testdata.file，键与 co/e2e/testdata.example.json 相同）。

新增-修改-删除用例的 codes 必须是账套里还没有的编码，写之前逐类用 SQL 核对。已有文件先留一份 .bak，
造数的键合并进去，文件里别的键保留。缺省路径（不带 lane 的配置共用的文件）和看不出通道的路径拒绝写。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from co.e2e.seed import data
from co.e2e.seed.base import Probe, Step, existing_codes
from co.e2e.seed.context import SeedContext, SeedError


def build() -> dict:
    cyc = {kind: list(codes) for kind, codes in data.CYCLE_CODES.items()}
    customer = {
        "codes": cyc["customer"],
        "existing": "C9999",
        "sort_code": data.CUSTOMER_CLASS,
        "super_dept": data.DEPT_SALES,
        "spec_operator": data.SPEC_OPERATOR,
        "spec_operator_other": data.SALES_PERSON,
        "trade_code": data.TRADE_CODE,
        "currency": data.CURRENCY,
    }
    person = {
        "codes": cyc["person"],
        "dept": data.DEPT_ARCHIVE,
        "employ_state": data.EMPLOY_STATE,
        "person_type": data.PERSON_TYPE,
        "employment_form": data.EMPLOYMENT_FORM,
    }
    archives = {
        "customer": customer,
        "vendor": {"codes": cyc["vendor"], "sort_code": data.VENDOR_CLASS, "currency": data.CURRENCY},
        "person": person,
        "inventory": {"codes": cyc["inventory"], "template": data.INV_TEMPLATE},
    }
    for kind in ("department", "warehouse", "customer_class", "vendor_class", "inventory_class"):
        archives[kind] = {"codes": cyc[kind]}
    return {
        "gl": dict(data.GL),
        "stock": {
            "transfer_out_rd": data.TRANSFER_OUT_RD,
            "transfer_in_rd": data.TRANSFER_IN_RD,
            "material_out_rd": data.MATERIAL_OUT_RD,
            "product_in_rd": data.PRODUCT_IN_RD,
        },
        "purchase_requisition": {"dept": data.DEPT_PURCHASE, "inventory": data.PLAIN_INVENTORY},
        "resolve": {"ambiguous_q": "有限公司"},
        "archives": archives,
    }


# 银行科目：账套科目表里 1002 下第一个末级科目（本币优先），造数时现查，不用通用占位值覆盖通道的配置。
_BANK_SQL = (
    "SELECT TOP (1) ccode AS code FROM code WHERE iyear = {year} AND bend = 1 AND ccode LIKE N'1002%'"
    " ORDER BY CASE WHEN ISNULL(cexch_name, N'') = N'' THEN 0 ELSE 1 END, ccode"
)


def lane_bank_account(ctx: SeedContext) -> str:
    """测试账套里的银行科目；查不到时返回空串。"""
    year = ctx.day[:4]
    if not year.isdigit():
        raise SeedError("日期不合法：" + ctx.day)
    rows = ctx.rows(_BANK_SQL.format(year=year))
    return rows[0].get("code", "").strip() if rows else ""


def gl_payload(ctx: SeedContext, target: Path) -> dict:
    """gl 段：配了 seed.gl.bank_account 就用它；否则用账套里查到的，查不到时保留通道文件已有的，
    文件里也没有才用 data.GL 的占位值。"""
    gl = dict(data.GL)
    if data.BANK_FIXED:
        return gl
    bank = lane_bank_account(ctx)
    if bank:
        gl["bank_account"] = bank
        return gl
    known = _read_existing(target).get("gl")
    if isinstance(known, dict) and known.get("bank_account"):
        del gl["bank_account"]
    return gl


def taken_cycle_codes(ctx: SeedContext) -> list[str]:
    taken = []
    for kind, codes in data.CYCLE_CODES.items():
        taken.extend(kind + ":" + code for code in sorted(existing_codes(ctx, kind, codes)))
    return taken


DEFAULT_TESTDATA = "~/.config/u8co/testdata.json"


def target_refusal(path: Path, cfg: object) -> str:
    """缺省路径是不带 lane 的配置共用的文件，不写；路径里要看得出是哪条通道（通道名或账套号）。"""
    if path.expanduser().resolve() == Path(DEFAULT_TESTDATA).expanduser().resolve():
        return "testdata.file 是缺省路径 " + DEFAULT_TESTDATA + "（不带 lane 的配置共用），给这条通道配一个自己的文件"
    tags = [str(getattr(cfg, key, "") or "") for key in ("lane", "account")]
    if not any(tag and tag in str(path) for tag in tags):
        return "testdata.file 的路径里没有通道名或账套号（" + "、".join(t for t in tags if t) + "），拒绝写"
    return ""


def merge(base: dict, extra: dict) -> dict:
    # 造数给出的键覆盖，文件里别的键（别的用例、手工加的）保留。
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_existing(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SeedError(f"读不了已有的测试数据 {path}: {type(exc).__name__}") from None
    if not isinstance(loaded, dict):
        raise SeedError("已有的测试数据不是 JSON 对象：" + str(path))
    return loaded


def write_testdata(path: Path, payload: dict, backup_first: bool = True) -> Path | None:
    # 只在造数之前的那一份上留 .bak；已有 .bak 不覆盖。
    current = _read_existing(path)
    backup = None
    if path.is_file() and backup_first:
        backup = path.with_name(path.name + ".bak")
        if not backup.exists():
            shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merge(current, payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return backup


def _conf() -> object:
    from co.e2e.settings import conf

    return conf()


def _probe(ctx: SeedContext) -> Probe:
    cfg = _conf()
    target = Path(str(getattr(cfg, "testdata_file", "")))
    problems = ["已被占用 " + item for item in taken_cycle_codes(ctx)]
    refused = target_refusal(target, cfg)
    if refused:
        problems.append(refused)
    return Probe((), tuple(problems) or (str(target),))


def emit(ctx: SeedContext, path: Path | None = None, cfg: object = None) -> tuple[int, int]:
    cfg = cfg or _conf()
    target = path or Path(str(getattr(cfg, "testdata_file", "")))
    refused = target_refusal(target, cfg)
    if refused:
        raise SeedError(refused)
    taken = taken_cycle_codes(ctx)
    if taken:
        raise SeedError("循环用例的编码在账套里已经有了，在 e2e 配置的 seed.cycle_codes 里换：" + "、".join(taken))
    payload = build()
    payload["gl"] = gl_payload(ctx, target)
    return (1, 0) if write_lane(ctx, target, payload, cfg) else (0, 1)


def write_lane(ctx: SeedContext, target: Path, payload: dict, cfg: object) -> bool:
    """把 payload 合并进通道的测试数据文件。返回 True 表示本会话第一次写（留了 .bak 或记下原来没有文件）。"""
    prior = ctx.state.get("testdata")
    if isinstance(prior, dict) and prior.get("file") == str(target):
        # 本会话已经写过一次：.bak 和「原来没有文件」的记录都沿用第一次的。
        write_testdata(target, payload, backup_first=False)
        _reload_testdata(target, cfg)
        return False
    existed = target.is_file()
    backup = write_testdata(target, payload)
    ctx.state["testdata"] = {"file": str(target), "backup": str(backup) if backup else "", "existed": existed}
    _reload_testdata(target, cfg)
    return True


def _reload_testdata(target: Path, cfg: object) -> None:
    # 后面的造数步骤（收付款单的银行科目等）按刚写的文件取值：写的是配置里的那份时清掉已读的缓存。
    from co.e2e import testdata

    if str(target) == str(getattr(cfg, "testdata_file", "")):
        testdata.use(None)


def reminder(state: dict) -> str:
    info = state.get("testdata")
    if not isinstance(info, dict) or not info.get("file"):
        return ""
    head = "提醒：不带 --keep 就 close 时，账套会还原，"
    if info.get("backup"):
        return head + "把 " + str(info["backup"]) + " 恢复成 " + str(info["file"])
    if not info.get("existed"):
        return head + "删掉造数写出的 " + str(info["file"])
    return head + "测试数据文件 " + str(info["file"]) + " 要自己改回"


STEP = Step("testdata", "测试数据文件", _probe, emit)
