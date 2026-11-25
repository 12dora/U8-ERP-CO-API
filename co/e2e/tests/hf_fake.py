"""voucher-head-fix 用例的单元测试替身：内存里的发货单、出库单、发票、往来明细、订单，和按合同实现的 vouchers/head_fix 假路由。

flags 里的开关用来造出有毛病的路由；Db.guarded 按关键字应答用例发的 SQL。不连接服务器。
"""

from __future__ import annotations

import copy
import hashlib
import re

from co.client.u8co_client import U8CoError
from co.e2e.casespec.head_fix_sql import PLAIN

NIL = "<nil>"
TABLE_OF = {"DispatchList": "dl", "rdrecord32": "rd", "SaleBillVouch": "sb", "Ar_Detail": "ar"}
_UNDO_TABLES = {"addr_code": ("rdrecord32", "SaleBillVouch"), "person_code": ("rdrecord32", "SaleBillVouch", "Ar_Detail")}
_COL = {"addr_code": "caddcode", "person_code": "cpersoncode"}


def norm(value: object) -> str:
    return "" if value is None else str(value)


class Db:
    """几张表。样本：1000 单一来源；1100/1101 合并发票；1200 地址 NULL；1300/1301 合并发票但 1301 已是新值；
    1400 出库单已是新值；1500 表头业务员为空；1600 原地址不在档案里；1700 原业务员无效（都不可回滚）；1800 未审核。"""

    def __init__(self) -> None:
        self.addrs = {"C900001": ["01", "02", "03"]}
        self.persons = {"P900001": "D901", "P900002": "D901", "P900003": "D902"}
        self.dl: dict[int, dict] = {}
        self.rd: dict[int, dict] = {}
        self.sb: dict[int, dict] = {}
        self.ar: dict[int, dict] = {}
        self.so: dict[int, dict] = {}
        self.rdl: list[tuple[int, int | None]] = []
        self.sbl: list[tuple[int, int | None]] = []
        self.picks: dict[str, str] = {
            "single": "1000\t02\tP900002",
            "merged": "1100\t02\tP900002",
            "merged_new": "1300\t02\tP900002",
            "already_new": "1400\t02\tP900002",
            "null": "1200\t02\tP900002",
            "empty": "1500\tx\tP900002",
            "not_rev_addr": "1600\t02\tx",
            "not_rev_person": "1700\tx\tP900002",
            "unaudited": "1800\t02\tP900002",
        }
        self.strict_empty = False
        self.clock = 0
        self.queries: list[str] = []
        self._fill()

    def _head(self, did: int, addr: str | None, person: str | None) -> None:
        self.dl[did] = {
            "cdlcode": f"FH{did}",
            "ccuscode": "C900001",
            "cdepcode": "D901",
            "caddcode": addr,
            "cpersoncode": person,
            "cverifier": "op001",
            "csocode": f"SO{did}",
            "cshipaddress": "ship",
            "cmemo": "m",
            "ufts": "t0",
        }
        self.so[did] = {"csocode": f"SO{did}", "cmemo": "so", "ufts": "t0"}

    def _down(self, kind: str, key: int, addr: str | None, person: str) -> None:
        base = {"caddcode": addr, "cpersoncode": person, "cmemo": "m", "ufts": "t0"}
        if kind == "rd":
            self.rd[key] = {**base, "ccode": f"XS{key}"}
        else:
            self.sb[key] = {**base, "csbvcode": f"SB{key}", "cvouchtype": "26"}

    def _ar(self, key: int, style: str, code: str, person: str) -> None:
        # 调整行（9M 等）和发票行同 cVouchType，靠处理方式 cProcStyle 区分
        self.ar[key] = {
            "cvouchtype": "26",
            "cvouchid": code,
            "cperson": person,
            "cprocstyle": style,
            "ccancelno": None,
            "cmemo": "m",
            "ufts": "t0",
        }

    def _fill(self) -> None:
        # 单一来源：一张出库单、一张发票（只由 1000 供货），发票有三行往来明细，另有一行别的发票的明细
        self._head(1000, "01", "P900001")
        self._down("rd", 2000, "01", "P900001")
        self._down("sb", 3000, "01", "P900001")
        self.rdl, self.sbl = [(2000, 1000)], [(3000, 1000)]
        self._ar(5000, "", "SB3000", "P900001")
        self._ar(5001, "9M", "SB3000", "P900001")
        self._ar(5002, "", "SB3000", "P900009")
        self._ar(5003, "", "SBOTHER", "P900001")
        # 合并发票：3100 由 1100 和 1101 两张发货单供货，1101 还是旧值
        self._head(1100, "01", "P900001")
        self._head(1101, "01", "P900001")
        self._down("rd", 2100, "01", "P900001")
        self._down("sb", 3100, "01", "P900001")
        self.rdl += [(2100, 1100)]
        self.sbl += [(3100, 1100), (3100, 1101)]
        self._ar(5100, "", "SB3100", "P900001")
        # 地址为 NULL
        self._head(1200, None, "P900001")
        self._down("rd", 2200, None, "P900001")
        self._down("sb", 3200, None, "P900001")
        self.rdl += [(2200, 1200)]
        self.sbl += [(3200, 1200)]
        # 合并发票，另一张发货单 1301 已经是新值：改完全部一致，发票要跟着改
        self._head(1300, "01", "P900001")
        self._head(1301, "02", "P900002")
        self._down("rd", 2300, "01", "P900001")
        self._down("sb", 3300, "01", "P900001")
        self.rdl += [(2300, 1300)]
        self.sbl += [(3300, 1300), (3300, 1301)]
        self._ar(5300, "", "SB3300", "P900001")
        # 出库单已经是新值，发票是旧值
        self._head(1400, "01", "P900001")
        self._down("rd", 2400, "02", "P900002")
        self._down("sb", 3400, "01", "P900001")
        self.rdl += [(2400, 1400)]
        self.sbl += [(3400, 1400)]
        self._ar(5400, "9M", "SB3400", "P900001")
        # 表头业务员为空
        self._head(1500, "01", None)
        # 原值不可回滚：地址不在客户的收货地址档案里；业务员不存在
        self._head(1600, "ZZ9", "P900001")
        self._head(1700, "01", "P9BAD")
        # 未审核
        self._head(1800, "01", "P900001")
        self.dl[1800]["cverifier"] = None

    def bump(self, row: dict) -> None:
        self.clock += 1
        row["ufts"] = f"t{self.clock}"

    def cells(self) -> dict[tuple[str, int, str], object]:
        return {
            (name, key, col): val
            for name in ("dl", "rd", "sb", "ar", "so")
            for key, row in getattr(self, name).items()
            for col, val in row.items()
            if col != "ufts"
        }

    # ---- 查询应答 ----

    def snapshot(self, did: int) -> str:
        rds = sorted({r for r, d in self.rdl if d == did})
        sbs = sorted({s for s, d in self.sbl if d == did})
        sib = sorted({d for s, d in self.sbl if s in sbs and d not in (None, did)})
        keys = {(self.sb[s]["cvouchtype"], self.sb[s]["csbvcode"]) for s in sbs}
        ars = sorted(k for k, row in self.ar.items() if (row["cvouchtype"], row["cvouchid"]) in keys)
        sos = sorted({next(k for k, row in self.so.items() if row["csocode"] == self.dl[did]["csocode"])})
        lines: list[str] = []
        for tag, table, ids in (
            ("DL", self.dl, [did]),
            ("DS", self.dl, sib),
            ("RD", self.rd, rds),
            ("SB", self.sb, sbs),
            ("AR", self.ar, ars),
            ("SO", self.so, sos),
        ):
            for key in ids:
                lines += [f"ROW:{tag}\t{key}\t{col}\t{self._show(col, val)}" for col, val in table[key].items()]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _show(col: str, val: object) -> str:
        if val is None:
            return NIL
        if col in {name.lower() for name in PLAIN}:
            return str(val)
        return "h:" + hashlib.sha256(str(val).encode("utf-8")).hexdigest()[:16]

    def guarded(self, _env: object, query: str, _label: str) -> str:
        self.queries.append(query)
        if "UNION ALL" in query:
            return self.snapshot(int(re.search(r"t\.DLID = (\d+)", query).group(1)))  # type: ignore[union-attr]
        if query.startswith("SET NOCOUNT ON;\nUPDATE dbo."):
            return self._reset(query)
        if query.startswith("SET NOCOUNT ON;\nSELECT TOP (1) N'ROW:' + CONVERT(varchar(20), d.DLID)"):
            return self._pick(query)
        if "MAX(DLID)" in query:
            return f"ROW:{max(self.dl) + 1000}\n"
        if "FROM dbo.Person WHERE cPersonCode" in query:
            code = re.search(r"cPersonCode = N'([^']*)'", query).group(1)  # type: ignore[union-attr]
            return f"ROW:{1 if code in self.persons else 0}\n"
        if "p.cDepCode <> d.cDepCode" in query:
            return "ROW:P900003\n"
        if "FROM dbo.CusDeliverAdd WHERE cCusCode" in query:
            hit = re.search(r"cCusCode = N'(\w+)' AND cAddCode = N'((?:[^']|'')*)'", query)
            return f"ROW:{1 if hit and hit.group(2) in self.addrs.get(hit.group(1), []) else 0}\n"
        raise AssertionError("没有料到的 SQL：" + query[:80])

    def _pick(self, query: str) -> str:
        if "(d.cVerifier IS NULL OR" in query:
            kind = "unaudited"
        elif "(d.cPersonCode IS NULL OR" in query:
            kind = "empty"
        elif "N'x' + NCHAR(9) + np.c" in query:
            kind = "not_rev_person"
        elif "na.c + NCHAR(9) + N'x'" in query:
            kind = "not_rev_addr"
        elif "SELECT TOP (1) o.caddcode AS c" in query:
            kind = "merged_new"
        elif "h.caddcode = na.c AND h.cPersonCode = np.c" in query:
            kind = "already_new"
        elif "COUNT(DISTINCT l2.DLID)" in query:
            kind = "merged"
        elif "AND d.caddcode IS NULL AND" in query:
            kind = "null"
        else:
            kind = "single"
        if kind == "single" and self.strict_empty and "a.cPerson = d.cPersonCode" in query:
            return ""
        return f"ROW:{self.picks[kind]}\n" if self.picks.get(kind) else ""

    def _reset(self, query: str) -> str:
        hit = re.search(r"UPDATE dbo\.(\w+) SET (.+)\nWHERE (\w+) = (\d+)", query)
        assert hit is not None, query
        table, sets, _key, row_id = hit.groups()
        row = getattr(self, TABLE_OF[table])[int(row_id)]
        for part in sets.split(", "):
            name, _eq, raw = part.partition(" = ")
            row[name.lower()] = None if raw == "NULL" else raw[2:-1].replace("''", "'")
        self.bump(row)
        return "ROW:1\n"


class Client:
    """按合同实现 vouchers/head_fix 的假路由（含 undo、already_new、person_empty）；flags 造出有毛病的路由。"""

    def __init__(self, db: Db, **flags: bool) -> None:
        self.db = db
        self.flags = flags
        self.calls: list[tuple[str, dict]] = []
        self.diffs: list[set[tuple[str, int, str]]] = []
        self.touched: dict[tuple[str, str], list[int]] = {}
        self.replies: list[dict | None] = []

    def health(self) -> dict:
        return {}

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, copy.deepcopy(fields)))
        if route != "/v1/vouchers/head_fix":
            raise U8CoError(404, "not_found", "路由不存在")
        before = self.db.cells()
        self.replies.append(None)
        try:
            out = self._fix(fields)
            self.replies[-1] = copy.deepcopy(out)
            return out
        finally:
            after = self.db.cells()
            self.diffs.append({cell for cell in set(before) | set(after) if before.get(cell) != after.get(cell)})

    def _check(self, fields: dict) -> tuple[dict, dict, dict]:
        if fields.get("type") != "dispatch":
            raise U8CoError(400, "bad_request", "type 只支持 dispatch")
        body, want = fields.get("fields"), fields.get("expect")
        if not isinstance(body, dict) or not body or set(body) - {"addr_code", "person_code"}:
            raise U8CoError(400, "bad_request", "fields 不合法")
        if not isinstance(want, dict) or set(want) != set(body) or body.get("person_code") == "":
            raise U8CoError(400, "bad_request", "expect 不合法")
        undo = fields.get("undo")
        if undo is not None:
            ok = isinstance(undo, dict) and not set(undo) - set(body)
            if not ok or any(set(tables) - set(_UNDO_TABLES[key]) for key, tables in undo.items()):
                raise U8CoError(400, "bad_request", "undo 不合法")
        row = self.db.dl.get(int(fields["id"]))
        if row is None:
            raise U8CoError(404, "not_found", "发货单不存在")
        return body, want, row

    def _fix(self, fields: dict) -> dict:
        body, want, row = self._check(fields)
        if not norm(row["cverifier"]).strip() and not self.flags.get("no_not_audited"):
            raise U8CoError(409, "not_audited", "发货单未审核")
        cur = {"addr_code": norm(row["caddcode"]), "person_code": norm(row["cpersoncode"])}
        if any(cur[key] != want[key] for key in body) and not self.flags.get("accept_mismatch"):
            raise U8CoError(409, "state_mismatch", "表头已变化")
        todo = {key: val for key, val in body.items() if cur[key] != val}
        self._validate(body, row, todo, cur)
        self._reversible(row, todo, cur, fields)
        did = int(fields["id"])
        out = {"ok": True, "type": "dispatch", "id": did, "code": row["cdlcode"], "changed": bool(todo)}
        before = {key: cur[key] for key in body}
        undo = None if self.flags.get("ignore_undo") else fields.get("undo")
        if undo is not None:
            self._verify(undo, want)
        if fields.get("dry_run"):
            if self.flags.get("dry_writes") and todo:
                self._set(row, todo)
            return {**out, "dry_run": True, "mode": "validate", "before": before}
        if not todo:
            return {**out, "before": before, "after": dict(before), "downstream": [], "undo": {}}
        self.touched = {}
        down = self._rollback(did, body, undo) if undo is not None else self._apply(did, todo, cur)
        after = {key: norm(row[_COL[key]]) for key in body}
        reply = {**out, "before": before, "after": after, "downstream": down}
        if not self.flags.get("drop_undo"):
            reply["undo"] = self._undo()
        return reply

    def _validate(self, body: dict, row: dict, todo: dict, cur: dict) -> None:
        if "person_code" in todo and not cur["person_code"] and not self.flags.get("no_person_empty"):
            raise U8CoError(409, "person_empty", "表头业务员为空，改了无法回滚")
        if body.get("addr_code") and body["addr_code"] not in self.db.addrs.get(row["ccuscode"], []):
            raise U8CoError(409, "address_not_found", "收货地址不存在")
        person = body.get("person_code")
        if person and person not in self.db.persons:
            raise U8CoError(409, "person_invalid", "业务员不存在")
        if person and self.db.persons[person] != row["cdepcode"]:
            raise U8CoError(409, "person_department_mismatch", "业务员不在发货单部门")

    def _reversible(self, row: dict, todo: dict, cur: dict, fields: dict) -> None:
        """正向修改前：原值写不回去（地址不在档案里、业务员无效）就拒绝，因为之后无法回滚。回滚调用（带 undo）不查。"""
        if self.flags.get("no_not_reversible") or fields.get("undo") is not None:
            return
        addrs = self.db.addrs.get(row["ccuscode"], [])
        if "addr_code" in todo and cur["addr_code"] and cur["addr_code"] not in addrs:
            raise U8CoError(409, "not_reversible", "原收货地址不在档案里，改了无法回滚")
        dept = self.db.persons.get(cur["person_code"])
        if "person_code" in todo and cur["person_code"] and dept != row["cdepcode"]:
            raise U8CoError(409, "not_reversible", "原业务员无效，改了无法回滚")

    def _undo(self) -> dict:
        out: dict = {}
        for (name, table), ids in sorted(self.touched.items()):
            out.setdefault(name, {})[table] = sorted(ids)
        return out

    def _touch(self, name: str, table: str, item: int) -> None:
        self.touched.setdefault((name, table), []).append(item)

    def _set(self, row: dict, todo: dict) -> None:
        for key, val in todo.items():
            self._put(row, _COL[key], val)
        self.db.bump(row)

    def _put(self, row: dict, col: str, val: str) -> None:
        empty = "" if self.flags.get("clear_to_empty") else None
        row[col] = val if val != "" else empty

    @staticmethod
    def _follows(row: dict, value: dict, todo: dict) -> bool:
        return all(norm(row[_COL[key]]).lower() == value[key].lower() for key in todo)

    def _table(self, name: str) -> dict:
        return {"rdrecord32": self.db.rd, "SaleBillVouch": self.db.sb, "Ar_Detail": self.db.ar}[name]

    def _verify(self, undo: dict, want: dict) -> None:
        """回滚：列出的每一行当前必须等于 expect 的值，否则整笔 409。"""
        for name, tables in undo.items():
            for table, ids in tables.items():
                for item in ids:
                    row = self._table(table).get(item)
                    col = "cperson" if table == "Ar_Detail" else _COL[name]
                    if row is None or norm(row[col]).lower() != want[name].lower():
                        raise U8CoError(409, "state_mismatch", f"{table} {item} 当前值与 expect 不符")

    def _rollback(self, did: int, body: dict, undo: dict) -> list[dict]:
        """只恢复 undo 列出的行，不再判断 shared / differs；表头照常写。"""
        self._set(self.db.dl[did], {key: val for key, val in body.items() if norm(self.db.dl[did][_COL[key]]) != val})
        names: dict[tuple[str, int], list[str]] = {}
        ar_codes: dict[str, int] = {}
        for name, tables in undo.items():
            for table, ids in tables.items():
                for item in ids:
                    row = self._table(table)[item]
                    self._put(row, "cperson" if table == "Ar_Detail" else _COL[name], body[name])
                    self.db.bump(row)
                    self._touch(name, table, item)
                    if table == "Ar_Detail":
                        ar_codes[row["cvouchid"]] = ar_codes.get(row["cvouchid"], 0) + 1
                    else:
                        names.setdefault((table, item), []).append(name)
        down = []
        for (table, item), keys in sorted(names.items()):
            code = self._table(table)[item].get("ccode" if table == "rdrecord32" else "csbvcode")
            down.append({"table": table, "id": item, "code": code, "action": "updated", "fields": keys})
        down += [{"table": "Ar_Detail", "invoice": code, "rows": n, "action": "updated"} for code, n in ar_codes.items()]
        return down

    def _apply(self, did: int, todo: dict, old: dict) -> list[dict]:
        db = self.db
        self._set(db.dl[did], todo)
        if self.flags.get("touch_ship"):
            db.dl[did]["cshipaddress"] = "changed"
        down = [self._rd(rid, did, todo, old) for rid in sorted({r for r, d in db.rdl if d == did})]
        for sid in sorted({s for s, d in db.sbl if d == did}):
            down += self._sb(sid, did, todo, old)
        return down

    def _mark(self, table: str, item: int, todo: dict) -> None:
        for key in todo:
            self._touch(key, table, item)

    def _rd(self, rid: int, did: int, todo: dict, old: dict) -> dict:
        row = self.db.rd[rid]
        entry = {"table": "rdrecord32", "id": rid, "code": row["ccode"]}
        if any(d != did for r, d in self.db.rdl if r == rid):
            return {**entry, "action": "skipped", "reason": "shared"}
        if self._follows(row, todo, todo):
            return {**entry, "action": "skipped", "reason": "already_new"}
        if not self._follows(row, old, todo):
            return {**entry, "action": "skipped", "reason": "differs"}
        self._set(row, todo)
        self._mark("rdrecord32", rid, todo)
        return {**entry, "action": "updated", "fields": list(todo)}

    def _sb(self, sid: int, did: int, todo: dict, old: dict) -> list[dict]:
        db = self.db
        row = db.sb[sid]
        entry = {"table": "SaleBillVouch", "id": sid, "code": row["csbvcode"]}
        srcs = [d for s, d in db.sbl if s == sid]
        others = {d for d in srcs if d not in (None, did)}
        shared = None in srcs or bool(others)
        agree = all(self._follows(db.dl[d], todo, todo) for d in others)
        forced = self.flags.get("force_invoice")
        if not forced and shared and not agree:
            return [{**entry, "action": "skipped", "reason": "shared"}]
        if not forced and self._follows(row, todo, todo):
            return [{**entry, "action": "skipped", "reason": "already_new"}]
        if not forced and not self._follows(row, old, todo):
            return [{**entry, "action": "skipped", "reason": "differs"}]
        self._set(row, todo)
        self._mark("SaleBillVouch", sid, todo)
        out = [{**entry, "action": "updated", "fields": list(todo)}]
        if "person_code" in todo and not self.flags.get("skip_ar"):
            out.append(self._ar_rows(row, todo, old))
        return out

    def _ar_rows(self, row: dict, todo: dict, old: dict) -> dict:
        hits = [
            (key, item)
            for key, item in self.db.ar.items()
            if item["cvouchtype"] == row["cvouchtype"]
            and item["cvouchid"] == row["csbvcode"]
            and item["cperson"] == old["person_code"]
        ]
        if self.flags.get("ar_foreign"):
            hits.append((5003, self.db.ar[5003]))
        for key, item in hits:
            item["cperson"] = todo["person_code"]
            self.db.bump(item)
            self._touch("person_code", "Ar_Detail", key)
        return {"table": "Ar_Detail", "invoice": row["csbvcode"], "rows": len(hits), "action": "updated"}
