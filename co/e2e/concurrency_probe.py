"""同一操作员并发登录的通道探针：只在打开的调试会话里跑，只调读路由。

    python -m co.e2e.concurrency_probe --n 6 --rounds 3 [--mode burst|hold]

通道按 session 的方式选：通道配置环境变量（见 settings）指向通道配置（~/.config/u8co/lanes/<acc>.json），
桥地址、服务密钥、操作员都经 settings.conf() 取，签名和连接走 U8CoClient，与 `session call` 同一条路。
burst：每轮同时发 N 个请求（login-check、小的 reports/mgmt/cash_stock、小页 archives/list 轮流），全是通道的那一个操作员。
hold：先发一个整份 cash_stock（五段），它还在跑时顺序发 3 次 login-check。
输出一张表和一行 JSON 汇总。退出码：0 没有异常；1 出现 422「登录状态不是 0」；2 其他意外（非 200，且不是 503 busy / busy_timeout；
429 busy 即队列已满，也按意外退出 2，缺省 --n 不会到这一步）。
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from co.e2e.callshape import encode_call_body, merge_call_fields
from co.e2e.cases import Operator, load_operator, scrub_text
from co.e2e.flow import local_day, make_remote, utc_stamp
from co.e2e.guard import assert_window
from co.e2e.lanelock import command_lock
from co.e2e.markers import read_linux_marker
from co.e2e.remote import RemoteError, release_all
from co.e2e.runfiles import run_tag
from co.e2e.settings import ConfigError, conf

LOGIN = "v1/login-check"
CASH = "v1/reports/mgmt/cash_stock"
ARC = "v1/archives/list"
LOGIN_STATE = "登录状态不是 0"
BUSY_CODES = frozenset(("busy", "busy_timeout"))
TIMEOUT = 180.0
HOLD_CHECKS = 3


class Probe:
    """一次运行的凭证和请求体模板。每个请求新建一个 U8CoClient，线程之间不共享连接。"""

    def __init__(self, operator: Operator, secret: str, period: int, archive: str, limit: int) -> None:
        self.operator = operator
        self.secret = secret
        self.today = local_day()
        self.small = {
            LOGIN: {},
            CASH: {"period": period, "sections": ["cash"]},
            ARC: {"archive": archive, "limit": limit},
        }
        self.full_cash = {"period": period}

    def needles(self) -> list[str]:
        return [self.operator.password, self.secret]

    def send(self, route: str, extra: dict, tag: str) -> dict:
        from co.client.u8co_client import U8CoClient, U8CoError, derive_keys, encrypt_password

        fields = merge_call_fields(dict(extra), self.operator, self.today)
        client = U8CoClient(conf().bridge_url, self.secret, timeout=TIMEOUT)
        _mac, k_enc = derive_keys(self.secret)
        body = encode_call_body(fields, encrypt_password(k_enc, self.operator.password))
        started = time.monotonic()
        try:
            payload = client._call("POST", "/" + route, body, True, timeout=TIMEOUT)
            status, code, message = 200, "", ""
            if payload.get("ok") is False:
                status, code, message = 0, str(payload.get("code") or ""), str(payload.get("message") or "")
        except U8CoError as exc:
            status, code, message = exc.status, str(exc.code or ""), str(exc.message or "")
        ms = int((time.monotonic() - started) * 1000)
        text = scrub_text(message, self.needles())[:160]
        return {"tag": tag, "route": route.removeprefix("v1/"), "status": status, "code": code, "message": text, "ms": ms}


def verdict(row: dict) -> str:
    if row["status"] == 422 and LOGIN_STATE in row["message"]:
        return "login_state"
    if row["status"] == 200:
        return "ok"
    if row["status"] == 503 and row["code"] in BUSY_CODES:
        return "busy"
    return "unexpected"


def run_burst(probe: Probe, n: int, rounds: int) -> list[dict]:
    routes = (LOGIN, CASH, ARC)
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=n) as pool:
        for rnd in range(1, rounds + 1):
            jobs = []
            for i in range(n):
                route = routes[i % len(routes)]
                jobs.append(pool.submit(probe.send, route, probe.small[route], f"r{rnd}.{i + 1}"))
            rows.extend(job.result() for job in jobs)
    return rows


def run_hold(probe: Probe, rounds: int) -> list[dict]:
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        for rnd in range(1, rounds + 1):
            long_job = pool.submit(probe.send, CASH, probe.full_cash, f"r{rnd}.hold")
            time.sleep(1.0)
            for i in range(HOLD_CHECKS):
                rows.append(probe.send(LOGIN, {}, f"r{rnd}.chk{i + 1}"))
            rows.append(long_job.result())
    return rows


def summarize(rows: list[dict], args: argparse.Namespace) -> dict:
    kinds = Counter(verdict(row) for row in rows)
    by_route: dict[str, dict] = {}
    for row in rows:
        item = by_route.setdefault(row["route"], {"count": 0, "max_ms": 0, "outcomes": Counter()})
        item["count"] += 1
        item["max_ms"] = max(item["max_ms"], row["ms"])
        item["outcomes"][f"{row['status']}/{row['code'] or '-'}"] += 1
    for item in by_route.values():
        item["outcomes"] = dict(item["outcomes"])
    return {
        "mode": args.mode,
        "n": args.n,
        "rounds": args.rounds,
        "total": len(rows),
        "verdicts": dict(kinds),
        "max_ms": max((row["ms"] for row in rows), default=0),
        "routes": by_route,
    }


def print_table(rows: list[dict]) -> None:
    print(f"{'tag':<10} {'route':<24} {'status':>6} {'ms':>7}  code / message")
    for row in rows:
        tail = (row["code"] + " " + row["message"]).strip()
        print(f"{row['tag']:<10} {row['route']:<24} {row['status']:>6} {row['ms']:>7}  {tail}")


def exit_code(summary: dict) -> int:
    kinds = summary["verdicts"]
    if kinds.get("login_state"):
        return 1
    if kinds.get("unexpected"):
        return 2
    return 0


def _save(report: Path, rows: list[dict], summary: dict) -> None:
    folder = report / "calls"
    folder.mkdir(exist_ok=True)
    path = folder / (utc_stamp().replace(":", "") + "-concurrency-probe-" + run_tag() + ".json")
    path.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _session_report() -> Path:
    local = read_linux_marker()
    if local is None:
        raise RuntimeError("没有打开的会话：先 python -m co.e2e.session open")
    report = Path(str(local.get("report_dir") or ""))
    if not report.is_dir():
        raise RuntimeError("会话目录不存在")
    assert_window(make_remote(), report)
    return report


def run(args: argparse.Namespace) -> int:
    from co.e2e.bridge_job import read_service_secret

    report = _session_report()
    operator = load_operator(Path(conf().operator_file))
    secret = read_service_secret(Path(conf().secret_file))
    period = args.period or int(local_day()[5:7])
    probe = Probe(operator, secret, period, args.archive, args.limit)
    rows = run_hold(probe, args.rounds) if args.mode == "hold" else run_burst(probe, args.n, args.rounds)
    summary = summarize(rows, args)
    print_table(rows)
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    _save(report, rows, summary)
    return exit_code(summary)


def _bounded(low: int, high: int):
    def parse(text: str) -> int:
        value = int(text)
        if value < low or value > high:
            raise argparse.ArgumentTypeError(f"取值 {low} 到 {high}")
        return value

    return parse


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="同一操作员并发登录探针（只读路由，只在打开的调试会话里跑）")
    parser.add_argument("--n", type=_bounded(1, 16), default=6, help="burst 每轮并发数，缺省 6")
    parser.add_argument("--rounds", type=_bounded(1, 20), default=3, help="轮数，缺省 3")
    parser.add_argument("--mode", choices=("burst", "hold"), default="burst")
    parser.add_argument("--period", type=_bounded(1, 12), default=0, help="cash_stock 的期间，缺省本月")
    parser.add_argument("--archive", default="department", help="archives/list 的档案，缺省 department")
    parser.add_argument("--limit", type=_bounded(1, 50), default=5, help="archives/list 每页条数，缺省 5")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        # 与 session call 共享通道锁：可以和 call、sql、audit 并行，不和 open、reset、close 等同时跑。
        with command_lock("call"):
            return run(args)
    except KeyboardInterrupt:
        print("收到中断")
        return 2
    except (ConfigError, RuntimeError, OSError, RemoteError) as exc:
        print(str(exc)[:300])
        return 2
    finally:
        release_all()


if __name__ == "__main__":
    raise SystemExit(main())
