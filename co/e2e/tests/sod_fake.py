"""voucher-so-defines 用例的单元测试替身：内存里的销售订单、订单行、发货单、发票、SO_SOMain 的列目录，
和按路由规格实现的 vouchers/so_defines 假路由（不含连带下游，见 sodc_fake）。

flags 里的开关用来造出有毛病的路由；Db.guarded 按关键字应答用例发的 SQL。不连接服务器。
"""

from __future__ import annotations

import copy
import re

from co.client.u8co_client import U8CoError

NIL = "<nil>"
_VALUE_MAX = 120
_EMPTY_MAX = 8
_KEY = re.compile(r"cdefine([1-9]|1[0-6])\Z")
_TABLES = ("so", "sd", "dl", "sb")
_TAGS = {"so": "SO", "sd": "SD", "dl": "DL", "sb": "SB"}
_ROUTE = "/v1/vouchers/so_defines"


def blank(value: str | None, empty: tuple[str, ...]) -> bool:
    text = "" if value is None else value.strip()
    return text == "" or text in empty


def same(one: str | None, two: str | None, empty: tuple[str, ...]) -> bool:
    if blank(one, empty) or blank(two, empty):
        return blank(one, empty) and blank(two, empty)
    return (one or "").strip() == (two or "").strip()


def exact(one: str | None, two: str | None) -> bool:
    return (one or "").strip() == (two or "").strip()


def bad(message: str, field: str = "") -> U8CoError:
    return U8CoError(400, "bad_request", message, field)


class Db:
    """订单 100：两列 NULL、未关闭、没有发货单；101：cdefine1 为「无」、已关闭；102：cdefine1 空串、已有发货单和发票；
    103：cdefine1 里已有值。列目录：cdefine1–3 文本 20 字符，cdefine4 日期型，其余文本 60 字符。"""

    def __init__(self) -> None:
        self.so: dict[int, dict] = {}
        self.sd: dict[int, dict] = {}
        self.dl: dict[int, dict] = {}
        self.sb: dict[int, dict] = {}
        self.dl_lines: list[tuple[int, int]] = []
        self.sb_lines: list[tuple[int, int]] = []
        self.picks = {"plain": [100], "plain_any": [100, 102], "closed": [101], "shipped": [102], "filled": [103]}
        self.cols = {f"cdefine{index}": (True, 20 if index <= 3 else 60) for index in range(1, 17)}
        self.cols["cdefine4"] = (False, 8)
        self.absent: set[str] = set()
        self.clock = 0
        self.queries: list[str] = []
        for key, define1, closer in ((100, None, None), (101, "无", "op2"), (102, "", None), (103, "KH-77", None)):
            self._order(key, define1, closer)
        self.so[100]["cdefine3"] = "C3 old"
        self.sd[1020]["cmemo"] = "line 1020"
        self.dl[7000] = {"cdlcode": "FH7000", "cmemo": "dl", "cdefine1": None, "ufts": "t0"}
        self.dl_lines.append((7000, 1020))
        self.sb[8000] = {"csbvcode": "SB8000", "cmemo": "sb", "cdefine1": None, "ufts": "t0"}
        self.sb_lines.append((8000, 1020))

    def _order(self, key: int, define1: str | None, closer: str | None) -> None:
        self.so[key] = {
            "csocode": f"SO{key}",
            "cverifier": "op1",
            "ccloser": closer,
            "clocker": None,
            "cdefine1": define1,
            "cdefine2": None,
            "cdefine3": None,
            "cmemo": "m",
            "cmodifier": None,
            "dmoddate": None,
            "ufts": "t0",
        }
        self.sd[key * 10] = {"id": key, "cinvcode": "INV1", "cmemo": "line", "ufts": "t0"}

    def bump(self, row: dict) -> None:
        self.clock += 1
        row["ufts"] = f"t{self.clock}"

    def cells(self) -> dict[tuple[str, int, str], object]:
        return {
            (name, key, col): val
            for name in _TABLES
            for key, row in getattr(self, name).items()
            for col, val in row.items()
            if col != "ufts"
        }

    # ---- 查询应答 ----

    def guarded(self, _env: object, query: str, _label: str) -> str:
        self.queries.append(query)
        if "UNION ALL" in query:
            return self.snapshot(int(re.search(r"t\.ID = (\d+)", query).group(1)))  # type: ignore[union-attr]
        if "SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), m.ID)" in query:
            return self._pick(query)
        if "SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), p.ID)" in query:
            return self.cascade_pick(query)
        if "MAX(ID)" in query:
            return f"ROW:{max(self.so) + 1000}\n"
        if "UserDef_Base" in query:
            return "".join(f"ROW:{col}\t{int(text)}\t{width}\t0\n" for col, (text, width) in self.cols.items())
        if query.startswith("SET NOCOUNT ON;\nUPDATE dbo.SO_SOMain SET "):
            return self._update(query)
        raise AssertionError("没有料到的 SQL：" + query[:80])

    def cascade_pick(self, _query: str) -> str:
        """连带下游的挑单：这个假库没有这类订单（sodc_fake.CascDb 才有）。"""
        return ""

    def _pick(self, query: str) -> str:
        if "BETWEEN 1 AND 40" in query:
            kind = "filled"
        elif "ISNULL(m.cCloser, N''))) <> N''" in query:
            kind = "closed"
        elif "AND EXISTS (SELECT 1 FROM dbo.DispatchLists" in query:
            kind = "shipped"
        elif "AND NOT EXISTS (SELECT 1 FROM dbo.DispatchLists" in query:
            kind = "plain"
        else:
            kind = "plain_any"
        if kind in self.absent:
            return ""
        skip = re.search(r"NOT IN \(([0-9, ]*)\)", query)
        gone = {int(item) for item in skip.group(1).split(",")} if skip else set()
        found = [key for key in self.picks[kind] if key not in gone]
        return f"ROW:{found[0]}\n" if found else ""

    def _update(self, query: str) -> str:
        key = int(re.search(r"WHERE ID = (\d+)", query).group(1))  # type: ignore[union-attr]
        row = self.so[key]
        sets = query.split(" SET ", 1)[1].split("\nWHERE", 1)[0]
        for col, raw, text in re.findall(r"(\w+) = (NULL|N'((?:[^']|'')*)')", sets):
            row[col.lower()] = None if raw == "NULL" else text.replace("''", "'")
        self.bump(row)
        return "ROW:1\n"

    @staticmethod
    def _show(value: object) -> str:
        return NIL if value is None else str(value)

    def snapshot(self, key: int) -> str:
        lines_of = {iso for iso, row in self.sd.items() if row["id"] == key}
        dls = sorted({d for d, iso in self.dl_lines if iso in lines_of})
        sbs = sorted({b for b, iso in self.sb_lines if iso in lines_of})
        out: list[str] = []
        for name, ids in (("so", [key]), ("sd", sorted(lines_of)), ("dl", dls), ("sb", sbs)):
            table = getattr(self, name)
            for row_id in ids:
                out += [f"ROW:{_TAGS[name]}\t{row_id}\t{col}\t{self._show(val)}" for col, val in table[row_id].items()]
        return "\n".join(out) + "\n"


class Ask:
    """校验过的请求：列键（小写，按请求顺序）、目标值、expect、空值标记。"""

    def __init__(self, fields: dict) -> None:
        if fields.get("type") != "sale_order":
            raise bad("type 只支持 sale_order", "type")
        empty = fields.get("empty_values") or []
        if not isinstance(empty, list) or len(empty) > _EMPTY_MAX or not all(isinstance(item, str) and item.strip() for item in empty):
            raise bad("empty_values 不合法", "empty_values")
        self.empty = tuple(item.strip() for item in empty)
        self.values = self._fields(fields.get("fields"))
        want = fields.get("expect")
        if not isinstance(want, dict) or {key.lower() for key in want} != set(self.values):
            raise bad("expect 的键须与 fields 相同", "expect")
        self.expect = {key.lower(): value for key, value in want.items()}
        if fields.get("dry_run") and fields.get("idempotency_key"):
            raise bad("预演不能带 Idempotency-Key")

    def _fields(self, raw: object) -> dict[str, str]:
        if not isinstance(raw, dict) or not 1 <= len(raw) <= 16:
            raise bad("fields 须有 1 到 16 项", "fields")
        out: dict[str, str] = {}
        for key, value in raw.items():
            low = key.lower()
            if _KEY.fullmatch(low) is None or low in out:
                raise bad("fields 有不允许的键 " + key, "fields")
            if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > _VALUE_MAX:
                raise bad("值不合法", "fields." + low)
            if value in self.empty:
                raise bad("值在 empty_values 里", "fields." + low)
            out[low] = value
        return out


class Client:
    """按路由规格实现 vouchers/so_defines 的假路由（含幂等重放、锁定、列目录、空值等价、区分大小写）。flags：
    no_replay、dry_writes、stamp、touch_lines、touch_dispatch、no_ufts、casefold（大小写不同当相等）、
    extra_current（响应 current 多带 cdefine1、cdefine2）、any_column（非文本列也收）、no_overwrite（已有值不覆盖）、
    no_field（列的 400 不带 field）。"""

    def __init__(self, db: Db, **flags: bool) -> None:
        self.db = db
        self.flags = flags
        self.calls: list[tuple[str, dict]] = []
        self.cache: dict[str, dict] = {}

    def health(self) -> dict:
        return {}

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, copy.deepcopy(fields)))
        if route != _ROUTE:
            raise U8CoError(404, "not_found", "路由不存在")
        key = fields.get("idempotency_key")
        if key and key in self.cache and not self.flags.get("no_replay"):
            return copy.deepcopy(self.cache[key])
        out = self._run(fields)
        if key and not fields.get("dry_run"):
            self.cache[key] = copy.deepcopy(out)
        return out

    def _columns(self, ask: Ask) -> None:
        for key, value in ask.values.items():
            where = "" if self.flags.get("no_field") else "fields." + key
            text, width = self.db.cols.get(key, (False, 0))
            if not text and self.flags.get("any_column"):
                continue
            if not text:
                raise bad("不是文本列", where)
            if width and len(value) > width:
                raise bad("超过自定义项长度", where)

    def _differs(self, row: dict, key: str, value: str) -> bool:
        if self.flags.get("casefold"):
            return (row.get(key) or "").strip().lower() != value.lower()
        if self.flags.get("no_overwrite") and not blank(row.get(key), ()):
            return False
        return not exact(row.get(key), value)

    def _check(self, row: dict, ask: Ask) -> list[str]:
        if row["clocker"] not in (None, ""):
            err = U8CoError(409, "state_mismatch", "单据已被锁定")
            err.detail = {"reason": "locked", "locker": row["clocker"].strip()}
            raise err
        self._columns(ask)
        if not all(same(ask.expect[key], row.get(key), ask.empty) for key in ask.values):
            err = U8CoError(409, "state_mismatch", "与 expect 不符")
            err.detail = {"current": {key: row.get(key) for key in ask.values}}
            raise err
        return [key for key, value in ask.values.items() if self._differs(row, key, value)]

    def _run(self, fields: dict) -> dict:
        ask = Ask(fields)
        so_id = int(fields["id"])
        row = self.db.so.get(so_id)
        if row is None:
            raise U8CoError(404, "not_found", "销售订单不存在")
        keys = self._check(row, ask)
        plan = self.plan_down(so_id, ask) if fields.get("cascade") else None
        out = {"ok": True, "type": "sale_order", "id": so_id, "code": row["csocode"], "changed": bool(keys)}
        if fields.get("dry_run"):
            if self.flags.get("dry_writes") and keys:
                self._write(row, ask, keys)
            return self._out(out, row, ask, plan, {"dry_run": True, "mode": "validate"})
        if keys:
            self._write(row, ask, keys)
        for item in plan or []:
            self.write_down(item, ask)
        return self._out(out, row, ask, plan, {})

    def _out(self, out: dict, row: dict, ask: Ask, plan: list[dict] | None, extra: dict) -> dict:
        shown = list(ask.values) + (["cdefine1", "cdefine2"] if self.flags.get("extra_current") else [])
        body = {**out, "current": {key: row.get(key) for key in shown}, "ufts": row["ufts"], **extra}
        if plan is not None:
            body["cascade"] = [{key: val for key, val in item.items() if key != "_tag"} for item in plan]
        return body

    def plan_down(self, so_id: int, ask: Ask) -> list[dict]:
        raise AssertionError("这个假路由不支持 cascade（用 sodc_fake.CascClient）")

    def write_down(self, item: dict, ask: Ask) -> None:
        raise AssertionError("这个假路由不支持 cascade")

    def _write(self, row: dict, ask: Ask, keys: list[str]) -> None:
        for key in keys:
            row[key] = ask.values[key]
        if not self.flags.get("no_ufts"):
            self.db.bump(row)
        if self.flags.get("stamp"):
            row["cmodifier"] = "svc"
            row["dmoddate"] = "2026-09-28T00:00:00"
        if self.flags.get("touch_lines"):
            for line in self.db.sd.values():
                line["cmemo"] = "changed"
        if self.flags.get("touch_dispatch"):
            for dispatch in self.db.dl.values():
                dispatch["cdefine1"] = ask.values.get("cdefine1")
