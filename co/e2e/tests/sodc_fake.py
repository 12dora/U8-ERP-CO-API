"""voucher-so-defines 用例连带下游一轮的单元测试替身：在 sod_fake 的假库上加销售出库单和连带下游用的订单与下游，
并按路由规格实现 cascade=true（只改请求的列、混合来源跳过）。flags 造出有毛病的路由。不连接服务器。

订单 200：发货单 7200、出库单 9200、发票 8200 都只来自它（pure）；订单 201：发货单 7201 另有订单 202 的明细、
发票 8202 有一行没有来源订单（都是混合来源），出库单 9201、发票 8201 只来自它（mixed）。
"""

from __future__ import annotations

import re

from co.e2e.tests.sod_fake import NIL, Ask, Client, Db, exact

_DOWN = {"DL": "dl", "RD": "rd", "SB": "sb"}
_TYPES = {"DL": "dispatch", "RD": "sale_out", "SB": "sale_invoice"}
_KEYS = {"DL": "DLID", "RD": "ID", "SB": "SBVID"}
_TABLE_TAG = {"DispatchList": "DL", "rdrecord32": "RD", "SaleBillVouch": "SB"}


class CascDb(Db):
    def __init__(self) -> None:
        super().__init__()
        self.rd: dict[int, dict] = {}
        self.rd_lines: list[tuple[int, int | None]] = []
        self.cascade_picks = {"pure": [200], "mixed": [201]}
        for key, define1 in ((200, "CT200"), (201, "CU201"), (202, "CT202")):
            self._order(key, define1, None)
        for vid, lines in ((7200, [2000]), (7201, [2010, 2020])):
            self.dl[vid] = self._down(f"FH{vid}")
            self.dl_lines += [(vid, iso) for iso in lines]
        for vid, lines in ((9200, [2000]), (9201, [2010])):
            self.rd[vid] = self._down(f"CK{vid}")
            self.rd_lines += [(vid, iso) for iso in lines]
        for vid, lines in ((8200, [2000]), (8201, [2010]), (8202, [2010, None])):
            self.sb[vid] = self._down(f"FP{vid}")
            self.sb_lines += [(vid, iso) for iso in lines]

    @staticmethod
    def _down(code: str) -> dict:
        return {"ccode": code, "cdefine1": "OLD-1", "cdefine2": None, "cdefine3": "D3", "cmodifier": "op1", "cmemo": "d", "ufts": "t0"}

    def cells(self) -> dict[tuple[str, int, str], object]:
        out = super().cells()
        out.update({("rd", key, col): val for key, row in self.rd.items() for col, val in row.items() if col != "ufts"})
        return out

    # ---- 关联 ----

    def _lines(self, tag: str) -> list[tuple[int, int | None]]:
        return {"DL": self.dl_lines, "RD": self.rd_lines, "SB": self.sb_lines}[tag]

    def sources(self, tag: str, vid: int) -> set[int | None]:
        return {None if iso is None else self.sd[iso]["id"] for one, iso in self._lines(tag) if one == vid}

    def downs(self, so_id: int) -> list[tuple[str, int]]:
        return sorted({(tag, vid) for tag in _DOWN for vid, _iso in self._lines(tag) if so_id in self.sources(tag, vid)})

    def row(self, tag: str, vid: int) -> dict:
        return getattr(self, _DOWN[tag])[vid]

    # ---- 查询应答 ----

    def guarded(self, env: object, query: str, label: str) -> str:
        if "TOP (1)" in query:
            return super().guarded(env, query, label)
        if "N'ROW:RD'" in query and "t.ID = " in query:
            self.queries.append(query)
            return self.cascade_snapshot(int(re.search(r"t\.ID = (\d+)", query).group(1)))  # type: ignore[union-attr]
        if "l2.DLID AS v" in query:
            self.queries.append(query)
            return self._mixed(int(re.search(r"\) <> (\d+)", query).group(1)))  # type: ignore[union-attr]
        hit = re.match(r"SET NOCOUNT ON;\nUPDATE dbo\.(DispatchList|rdrecord32|SaleBillVouch) SET ", query)
        if hit:
            self.queries.append(query)
            return self._restore_down(_TABLE_TAG[hit.group(1)], query)
        return super().guarded(env, query, label)

    def cascade_pick(self, query: str) -> str:
        kind = "pure" if "NOT EXISTS" in query else "mixed"
        if "cascade-" + kind in self.absent:
            return ""
        skip = re.search(r"p\.ID NOT IN \(([0-9, ]*)\)", query)
        gone = {int(item) for item in skip.group(1).split(",")} if skip else set()
        found = [key for key in self.cascade_picks[kind] if key not in gone]
        return f"ROW:{found[0]}\n" if found else ""

    def cascade_snapshot(self, key: int) -> str:
        lines = sorted(iso for iso, row in self.sd.items() if row["id"] == key)
        out = [f"ROW:SO\t{key}\t{col}\t{self._show(val)}" for col, val in self.so[key].items()]
        out += [f"ROW:SD\t{iso}\t{col}\t{self._show(val)}" for iso in lines for col, val in self.sd[iso].items()]
        for tag, vid in self.downs(key):
            out += [f"ROW:{tag}\t{vid}\t{col}\t{self._show(val)}" for col, val in self.row(tag, vid).items()]
        return "\n".join(out) + "\n"

    def _mixed(self, key: int) -> str:
        rows = [f"ROW:{tag}\t{vid}" for tag, vid in self.downs(key) if self.sources(tag, vid) != {key}]
        return "\n".join(rows) + "\n"

    def _restore_down(self, tag: str, query: str) -> str:
        vid = int(re.search(rf"WHERE {_KEYS[tag]} = (\d+)", query).group(1))  # type: ignore[union-attr]
        row = self.row(tag, vid)
        sets = query.split(" SET ", 1)[1].split("\nWHERE", 1)[0]
        for col, raw, text in re.findall(r"(\w+) = (NULL|N'((?:[^']|'')*)')", sets):
            row[col.lower()] = None if raw == "NULL" else text.replace("''", "'")
        self.bump(row)
        return "ROW:1\n"


class CascClient(Client):
    """在 sod_fake.Client 上按路由规格加 cascade。flags：cascade_mixed 混合来源也改、cascade_stamp 下游也盖修改人、
    no_cascade 报 updated 却不写下游、cascade_all 下游连没请求的 cdefine3 也写。"""

    db: CascDb

    def plan_down(self, so_id: int, ask: Ask) -> list[dict]:
        return [self._decide(so_id, tag, vid, ask) for tag, vid in self.db.downs(so_id)]

    def _agrees(self, src: int | None, so_id: int, ask: Ask) -> bool:
        if src == so_id:
            return True
        return src is not None and all(exact(self.db.so[src].get(key), value) for key, value in ask.values.items())

    def _decide(self, so_id: int, tag: str, vid: int, ask: Ask) -> dict:
        row = self.db.row(tag, vid)
        before = {key: row.get(key) for key in ask.values}
        item = {"type": _TYPES[tag], "id": vid, "code": row["ccode"], "before": before, "_tag": tag}
        if all(exact(row.get(key), value) for key, value in ask.values.items()):
            return {**item, "result": "unchanged"}
        agree = all(self._agrees(src, so_id, ask) for src in self.db.sources(tag, vid))
        if not agree and not self.flags.get("cascade_mixed"):
            return {**item, "result": "skipped", "reason": "mixed_sources"}
        return {**item, "result": "updated"}

    def write_down(self, item: dict, ask: Ask) -> None:
        if item["result"] != "updated" or self.flags.get("no_cascade"):
            return
        row = self.db.row(item["_tag"], item["id"])
        row.update(ask.values)
        if self.flags.get("cascade_all"):
            row["cdefine3"] = "ALL"
        if self.flags.get("cascade_stamp"):
            row["cmodifier"] = "svc"
        self.db.bump(row)


__all__ = ["NIL", "CascClient", "CascDb"]
