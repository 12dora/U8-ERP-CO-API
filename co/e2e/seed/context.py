"""造数的上下文：签名的桥调用、会话 SQL 通道、进度状态和调用记录。

桥调用和 session call 走同一段代码（session_call._post），SQL 和 session sql 走同一条通道（session_io._run_sql），
都先过 sqlguard。请求、响应、SQL 和输出擦掉口令和密钥后写进 <会话目录>/seed-calls/。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from co.e2e.cases import public_copy, scrub_text
from co.e2e.remote import RemoteResult
from co.e2e.sqlguard import sql_block_reason

_DASHES = re.compile(r"^-+(\t-+)*\Z")
_AFFECTED = re.compile(r"^\(\d+ (rows? affected|行受影响)\)")
_SAFE = re.compile(r"[^A-Za-z0-9_-]")


class SeedError(RuntimeError):
    def __init__(self, text: str, response: dict | None = None, saved: str = "") -> None:
        super().__init__(text)
        self.response = response or {}
        self.saved = saved


@dataclass
class Link:
    # 连到服务器的几样东西；单元测试换成假的。
    post: Callable[[str, dict], dict]
    sql: Callable[[str], RemoteResult]
    window: Callable[[], None]
    operator: str
    day: str
    needles: list[str] = field(default_factory=list)
    # comprobe 探针（co.probe.comprobe.run_steps）：步骤 → （退出码，输出）。只有要 U8 自己的连接时用（例如 EXEC sp_GetID）。
    probe: Callable[[list[dict]], tuple[int, str]] | None = None


def parse_table(text: str) -> list[dict[str, str]]:
    """sqlcmd -W -s "\\t" 的文本输出：表头、横线、数据行。只取第一个结果集，NULL 当空串。"""
    lines = [line.rstrip("\r") for line in text.lstrip("﻿").splitlines()]
    start = -1
    for i in range(len(lines) - 1):
        if lines[i].strip() and _DASHES.fullmatch(lines[i + 1].strip()):
            start = i
            break
    if start < 0:
        return []
    header = lines[start].split("\t")
    rows = []
    for line in lines[start + 2 :]:
        # 全是制表符的行是一行空值，不是结果集的结尾。
        if line == "" or _AFFECTED.match(line):
            break
        cells = line.split("\t")
        cells += [""] * (len(header) - len(cells))
        rows.append({name: ("" if cell == "NULL" else cell) for name, cell in zip(header, cells)})
    return rows


def parse_probe(text: str) -> list[dict]:
    """comprobe.exe 的输出：每步一行 JSON 对象；别的行（进度、警告）跳过。"""
    lines = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            lines.append(item)
    return lines


def _load_state(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


class SeedContext:
    def __init__(self, report_dir: Path, link: Link) -> None:
        self.report_dir = report_dir
        self.link = link
        self.day = link.day
        self.operator = link.operator
        self.calls_dir = report_dir / "seed-calls"
        self.state_path = report_dir / "seed-state.json"
        self.state = _load_state(self.state_path)
        self.last_call = ""
        self._n = 0

    def _file(self, stem: str) -> Path:
        self.calls_dir.mkdir(parents=True, exist_ok=True)
        self._n += 1
        return self.calls_dir / f"{self._n:04d}-{_SAFE.sub('-', stem)[:60]}"

    def call(self, path: str, body: dict) -> dict:
        needles = self.link.needles
        response = self.link.post(path, body)
        target = self._file(path.strip("/").replace("/", "-")).with_suffix(".json")
        payload = {"path": path, "request": public_copy(body, needles), "response": public_copy(response, needles)}
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.last_call = str(target)
        return response

    def _sql(self, query: str, label: str) -> str:
        text = "SET NOCOUNT ON;\n" + query
        reason = sql_block_reason(text)
        if reason:
            raise SeedError("SQL 被守卫拒绝：" + reason)
        base = self._file(label)
        base.with_suffix(".sql").write_text(text, encoding="utf-8")
        result = self.link.sql(text)
        out = scrub_text(result.out, self.link.needles)
        base.with_suffix(".out.txt").write_text(out, encoding="utf-8")
        self.last_call = str(base.with_suffix(".out.txt"))
        if result.code != 0:
            raise SeedError("SQL 失败（退出码 " + str(result.code) + "）：" + out.strip()[:200], saved=self.last_call)
        return out

    def probe(self, steps: list[dict], label: str) -> list[dict]:
        """经 comprobe 跑一段步骤（校验同 comprobe run），步骤和输出擦掉口令后写进 seed-calls/；任何一步失败都停下。"""
        from co.probe.comprobe import validate_script

        if self.link.probe is None:
            raise SeedError("这条造数链路没有 comprobe 探针")
        try:
            validate_script({"steps": steps})
        except ValueError as exc:
            raise SeedError("探针脚本不合法：" + str(exc)) from None
        code, out = self.link.probe(steps)
        text = scrub_text(out, self.link.needles)
        target = self._file("probe-" + label).with_suffix(".json")
        target.write_text(json.dumps({"steps": steps, "exit": code, "output": text}, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
        self.last_call = str(target)
        lines = parse_probe(text)
        failed = [line for line in lines if line.get("ok") is not True]
        if code != 0 or failed or not lines:
            why = str((failed[0].get("message") if failed else "") or text.strip()[:200] or "没有输出")
            raise SeedError("探针失败（退出码 " + str(code) + "）：" + why[:300], saved=self.last_call)
        return lines

    def rows(self, query: str) -> list[dict[str, str]]:
        return parse_table(self._sql(query, "select"))

    def exec(self, sql: str) -> None:
        self._sql(sql, "exec")

    def save_state(self) -> None:
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def refusal(marker: dict | None, cfg: object) -> str:
    # 造数只在调试会话里做，而且这条通道的配置必须明说可以保留（account.seedable）；不按账套号判断。
    if marker is None:
        return "没有打开的会话。先 python -m co.e2e.session open"
    owner = str(marker.get("owner") or "")
    if owner != "session":
        return "会话所有者是 " + (owner or "未知") + "，不是 session：造数只在调试会话里做"
    if not Path(str(marker.get("report_dir") or "")).is_dir():
        return "会话目录不存在"
    if getattr(cfg, "seedable", False) is not True:
        return "这条通道的配置没有打开 account.seedable，拒绝造数"
    return ""


def live_link(report_dir: Path) -> Link:
    from co.e2e.bridge_job import read_service_secret
    from co.e2e.callshape import merge_call_fields, normalize_call_path
    from co.e2e.cases import load_operator
    from co.e2e.flow import local_day, make_remote
    from co.e2e.guard import assert_window
    from co.e2e.runfiles import run_tag, tagged
    from co.e2e.session_call import _post
    from co.e2e.session_io import _run_sql
    from co.e2e.settings import conf
    from co.probe.comprobe import run_steps

    operator = load_operator(Path(conf().operator_file))
    secret = read_service_secret(Path(conf().secret_file))
    remote = make_remote()
    day = local_day()

    def post(path: str, body: dict) -> dict:
        suffix = normalize_call_path(path)
        if suffix == "":
            raise SeedError("路径不合法 " + path)
        return _post(secret, suffix, merge_call_fields(body, operator, day), operator)

    def sql(query: str) -> RemoteResult:
        # 退回旧脚本时要上传的查询文件；文件名带本次调用的标记，和并行的 session sql 不冲突。
        local = report_dir / tagged(run_tag(), "seed-query.sql")
        local.write_text(query, encoding="utf-8")
        try:
            return _run_sql(remote, local, conf().data_db, query)
        finally:
            local.unlink(missing_ok=True)

    def window() -> None:
        assert_window(remote, report_dir)

    return Link(post, sql, window, operator.code, day, [operator.password, secret], run_steps)
