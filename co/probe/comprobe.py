# JSON 驱动的测试账套 COM 探测。口令只走 stdin。站点值来自 co.e2e.settings（e2e 配置）。
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from co.e2e.cases import load_operator
from co.e2e.lanelock import session_lock
from co.e2e.settings import conf
from co.probe.comprobe_remote import build_body, ensure_body, launch_body, probe_config, remote_ssh, upload_sources

__all__ = ["build_body", "ensure_body", "launch_body", "probe_config"]

_ROOT = Path(__file__).resolve().parents[2]
_WRITE = frozenset({"call", "trans.begin", "trans.commit", "trans.rollback", "dom.set"})
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\Z")
_REF = re.compile(r"^\$[A-Za-z_][A-Za-z0-9_]*\Z")
# 口令占位符：只能当 call 的参数（comprobe.exe 同样只在 call 里认它），别处一律拒绝，免得 sql、set 把口令回显出来。
_SECRET = "$__secret"
_PROGID = re.compile(r"^(?:[A-Za-z][A-Za-z0-9_.]{0,120}|\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\})\Z")  # ProgID，或 {CLSID}
_SUB = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,15}\Z")
_OP = re.compile(r"^[A-Za-z0-9_-]{1,20}\Z")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
_SERVER = re.compile(r"^[A-Za-z0-9._-]{1,80}\Z")
_KEYS = {
    "login": {"op", "sub", "year", "operator", "acc", "account", "server", "date", "var"},
    # hold：连续登录 count 次并保持，记下成功次数，最后全部登出（测许可剩余点数）。
    "hold": {"op", "sub", "count", "year", "operator", "acc", "account", "server", "date"},
    "create": {"op", "progid", "var"},
    # tolerate：这一步失败时照常输出错误行，然后继续后面的步骤（便于失败后再查 lastsql、回滚）；
    # 跑完后退出码为 2（不是 0）。写步骤时间预算用完的拒绝从不容忍，立即停下（退出码 1）。
    "call": {"op", "target", "method", "args", "byref", "dispatch_null", "var", "tolerate"},
    "get": {"op", "target", "prop", "var"},
    "set": {"op", "target", "prop", "value"},
    "dom.new": {"op", "var"},
    "dom.blank": {"op", "sql", "var"},
    "dom.load": {"op", "sql", "id", "var"},
    "dom.addrow": {"op", "dom", "var"},
    "dom.rows": {"op", "dom", "cols", "max"},
    "dom.set": {"op", "dom", "row", "attr", "value"},
    "dom.single": {"op", "dom", "row", "var"},
    "dom.xml": {"op", "dom", "max"},
    "sql": {"op", "query", "params", "max", "timeout_s"},
    "trans.begin": {"op"},
    "trans.commit": {"op"},
    "trans.rollback": {"op"},
    "lastsql": {"op"},
}
_NEED = {
    "login": ("sub",),
    "hold": ("sub", "count"),
    "create": ("progid", "var"),
    "call": ("target", "method"),
    "get": ("target", "prop"),
    "set": ("target", "prop", "value"),
    "dom.new": ("var",),
    "dom.blank": ("sql", "var"),
    "dom.load": ("sql", "id", "var"),
    "dom.addrow": ("dom",),
    "dom.rows": ("dom", "cols"),
    "dom.set": ("dom", "row", "attr", "value"),
    "dom.single": ("dom", "row", "var"),
    "dom.xml": ("dom",),
    "sql": ("query",),
}


def load_script(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    if len(raw.encode("utf-8")) > 1024 * 1024:
        raise ValueError("脚本超过 1 MiB")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("脚本不是 JSON") from None
    if not isinstance(data, dict):
        raise ValueError("脚本必须是含 steps 数组的对象")
    return data


def validate_script(data: object) -> list[dict]:
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
        raise ValueError("脚本必须是含 steps 数组的对象")
    steps = data["steps"]
    for index, step in enumerate(steps):
        _step(index, step)
    return steps


def script_writes(steps: list[dict]) -> bool:
    for step in steps:
        if step.get("op") in _WRITE:
            return True
    return False


def parse_status(text: str) -> dict[str, str]:
    info: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(" ", 1)
        if len(parts) == 2:
            info[parts[0]] = parts[1].strip()
    return info


def write_block_reason(steps: list[dict], status_text: str) -> str:
    if not script_writes(steps):
        return ""
    return window_reason(parse_status(status_text))


def window_reason(info: dict[str, str]) -> str:
    snaps = [part for part in info.get("snapshots", "").split(",") if part and part != "none"]
    for name in conf().snapshots:
        if name not in snaps:
            return "快照不在，拒绝执行"
    if info.get("done") != "no":
        return "看门狗已还原，拒绝执行"
    left = info.get("minutes_left", "")
    if not left.isdigit() or int(left) < 5:
        return "看门狗剩余不足 5 分钟"
    return ""


def scrub_output(text: str, password: str) -> str:
    if not password:
        return text
    return text.replace(password, "***")


def stdin_payload(password: str, script: str) -> bytes:
    return (password + "\n" + script).encode("utf-8")


def write_budget(info: dict[str, str]) -> int:
    # 写步骤的时间预算（秒）：看门狗到点前 3 分钟为止，最多 900 秒（与远程命令的超时一致）。
    # comprobe.exe 每个写步骤之前核对，超出就拒绝继续写，避免看门狗在探针写到一半时还原。
    left = info.get("minutes_left", "")
    if not left.isdigit():
        return 60
    return max(60, min(900, (int(left) - 3) * 60))


def prepare_script(data: dict, year: str, operator: str, budget: int = 0) -> dict:
    steps = []
    for step in data["steps"]:
        if step.get("op") not in ("login", "hold"):
            steps.append(step)
            continue
        login = dict(step)
        login.pop("account", None)
        login["acc"] = conf().account
        if not login.get("year"):
            login["year"] = year
        if not login.get("operator"):
            login["operator"] = operator
        steps.append(login)
    doc: dict = {"steps": steps}
    if budget > 0:
        doc["write_budget_s"] = budget
    return doc


def _step(index: int, step: object) -> None:
    if not isinstance(step, dict):
        raise ValueError("第%d步不是对象" % (index + 1))
    if "password" in step:
        raise ValueError("脚本不能包含口令")
    op = step.get("op")
    if op not in _KEYS:
        raise ValueError("第%d步不认识操作" % (index + 1))
    extra = set(step) - _KEYS[op]
    if extra:
        raise ValueError("第%d步含未知字段" % (index + 1))
    _need(index, step, op)
    if "tolerate" in step and not isinstance(step["tolerate"], bool):
        raise ValueError("tolerate 应为布尔值")
    if op in ("login", "hold"):
        _login(step)
        if op == "hold" and (not _is_int(step["count"]) or not 1 <= step["count"] <= 20):
            raise ValueError("count 应为 1 到 20")
        return
    _forms(step, op)


def _need(index: int, step: dict, op: str) -> None:
    for key in _NEED.get(op, ()):
        if key not in step:
            raise ValueError("第%d步缺少 %s" % (index + 1, key))


def _login(step: dict) -> None:
    _match(step["sub"], _SUB, "子系统无效")
    _opt(step, "year", _year)
    _opt(step, "operator", lambda text: _match(text, _OP, "操作员无效"))
    _opt(step, "date", lambda text: _match(text, _DATE, "日期格式应为 yyyy-MM-dd"))
    _opt(step, "server", lambda text: _match(text, _SERVER, "服务器名无效"))
    _opt(step, "var", _ident)
    _acc(step.get("acc", conf().account))
    if "account" in step:
        _acc(step.get("account"))


def _forms(step: dict, op: str) -> None:
    for key, check in (("var", _ident), ("method", _ident), ("prop", _ident), ("attr", _ident)):
        _opt(step, key, check)
    _opt(step, "progid", lambda text: _match(text, _PROGID, "ProgID 无效"))
    _opt(step, "target", _ref)
    _opt(step, "dom", _ref)
    _lists(step)
    _texts(step, op)


def _lists(step: dict) -> None:
    if "row" in step:
        _row(step["row"])
    if "args" in step:
        _args(step["args"], step.get("op") == "call")
    if "params" in step:
        _args(step["params"], False)
    _refs(step)


def _texts(step: dict, op: str) -> None:
    if "id" in step:
        _num(step["id"], "id 无效")
    if "max" in step:
        _max(step["max"], op)
    if "sql" in step:
        _sql(step["sql"])
    if "query" in step:
        _sql(step["query"])
    if "cols" in step:
        _cols(step["cols"])
    if "value" in step and step["value"] is not None:
        _arg(step["value"], False)


def _refs(step: dict) -> None:
    count = len(step["args"]) if isinstance(step.get("args"), list) else 0
    for key in ("byref", "dispatch_null"):
        if key not in step:
            continue
        _indexes(step[key], count)


def _args(value: object, secret_ok: bool) -> None:
    if not isinstance(value, list):
        raise ValueError("参数必须是数组")
    for item in value:
        _arg(item, secret_ok)


def _arg(value: object, secret_ok: bool) -> None:
    if value is None or isinstance(value, bool) or isinstance(value, (int, float, str)):
        if value == _SECRET:
            if not secret_ok:
                raise ValueError("$__secret 只能当 call 的参数")
            return
        if isinstance(value, str) and value.startswith("$"):
            _ref(value)
        return
    if isinstance(value, dict):
        _typed(value)
        return
    raise ValueError("参数形式无效")


def _typed(value: dict) -> None:
    if len(value) != 1:
        raise ValueError("参数形式无效")
    key = next(iter(value))
    if key in ("short", "int"):
        _num(value[key], "需要整数")
        return
    if key == "bool" and isinstance(value[key], bool):
        return
    if key == "date" and isinstance(value[key], str) and _DATE.match(value[key]):
        return
    raise ValueError("参数形式无效")


def _indexes(value: object, count: int) -> None:
    if not isinstance(value, list):
        raise ValueError("引用参数序号无效")
    for item in value:
        if not _is_int(item) or item < 0 or item >= count:
            raise ValueError("引用参数序号无效")


def _row(value: object) -> None:
    if isinstance(value, str):
        _ref(value)
        return
    if _is_int(value) and value >= 0:
        return
    raise ValueError("行引用无效")


def _cols(value: object) -> None:
    if value == "*":
        return
    if isinstance(value, str):
        _names(value.split(","))
        return
    if isinstance(value, list):
        _names([str(item) for item in value])
        return
    raise ValueError("cols 无效")


def _names(parts: list[str]) -> None:
    for part in parts:
        if part != "":
            _ident(part)


def _max(value: object, op: str) -> None:
    cap = 20000 if op == "dom.xml" else 200
    if not _is_int(value) or value < 1 or value > cap:
        raise ValueError("max 无效")


def _sql(value: object) -> None:
    if not isinstance(value, str) or value == "" or len(value) > 200000 or "\x00" in value:
        raise ValueError("SQL 无效")


def _acc(value: object) -> None:
    code = conf().account
    if value != code and not (_is_int(value) and str(value).zfill(3) == code):
        raise ValueError("只允许测试账套 " + code)


def _year(text: object) -> None:
    if not isinstance(text, str) or re.fullmatch(r"\d{4}", text) is None:
        raise ValueError("年度必须是 4 位数字")


def _opt(step: dict, key: str, check) -> None:
    if key in step and step[key] is not None:
        check(step[key])


def _ident(text: object) -> None:
    _match(text, _IDENT, "名字无效")


def _ref(text: object) -> None:
    if text == _SECRET:
        raise ValueError("$__secret 只能当 call 的参数")
    _match(text, _REF, "需要变量引用")


def _match(text: object, pattern: re.Pattern[str], message: str) -> None:
    if not isinstance(text, str) or pattern.fullmatch(text) is None:
        raise ValueError(message)


def _num(value: object, message: str) -> None:
    if not _is_int(value):
        raise ValueError(message)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="comprobe", description="测试账套上的 JSON COM 探测")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="上传源码并在服务器上编译")
    run_parser = sub.add_parser("run", help="把脚本连同口令送到已编译的探测程序")
    run_parser.add_argument("--script", required=True)
    return parser


def _main(args: argparse.Namespace) -> int:
    if args.cmd == "build":
        return build()
    return run(Path(args.script))


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(_parser().parse_args(argv))
    except (RuntimeError, OSError, ValueError) as exc:
        print(str(exc)[:300])
        return 1


def build() -> int:
    # 编译会重锁远程工作目录：与同一通道的 session 命令互斥（co/e2e/lanelock.py）。
    with session_lock(True):
        _ensure()
        upload_sources()
        code, out = remote_ssh(build_body(), None, 180)
    print(out)
    return code


def run(path: Path) -> int:
    code, out = run_steps(validate_script(load_script(path)))
    print(out)
    return code


def run_steps(steps: list[dict]) -> tuple[int, str]:
    """把校验过的步骤送给 comprobe.exe，返回（退出码，擦掉口令的输出）。造数（co.e2e.seed）也经这里调探针。"""
    # 与 call、sql 一样共享锁：会话 close（独占）不会在探针写到一半时还原。
    with session_lock(False):
        status = _status_text() if script_writes(steps) else ""
        reason = write_block_reason(steps, status)
        if reason:
            return 1, reason
        operator = load_operator(Path(conf().operator_file))
        budget = write_budget(parse_status(status)) if status else 0
        script = json.dumps(prepare_script({"steps": steps}, operator.year, operator.code, budget), ensure_ascii=False)
        code, out = remote_ssh(launch_body(), stdin_payload(operator.password, script), 900)
    return code, scrub_output(out, operator.password)


def _status_text() -> str:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "co.e2e.session", "status"],
            cwd=str(_ROOT),
            capture_output=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("查看调试会话超时") from None
    from co.e2e.remote import decode_output

    return decode_output(proc.stdout) + decode_output(proc.stderr)


def _ensure() -> None:
    code, out = remote_ssh(ensure_body(), None, 60)
    if code != 0:
        raise RuntimeError(out[:300] or "创建目录失败")


if __name__ == "__main__":
    raise SystemExit(main())
