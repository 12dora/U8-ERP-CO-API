"""登录、错误签名和名单外账套。名单外账套用假操作员，避免真实编码进入审计。"""

from __future__ import annotations

from co.client.u8co_client import U8Call, U8CoClient, U8CoError, U8CoLoginFailed
from co.e2e.cases import Operator, should_retry_year, year_plan
from co.e2e.settings import conf


def make_call(spec: dict) -> U8Call:
    operator = spec["operator"]
    return U8Call(
        acc=spec.get("acc") or operator.acc,
        year=spec["year"],
        operator=operator.code,
        password=operator.password,
        date=spec["today"],
        doc_id=spec.get("doc_id"),
        action=spec.get("action") or "",
    )


def try_login(client: U8CoClient, operator: Operator, today: str) -> tuple[str, dict | None, str]:
    years = year_plan(operator.year, today)
    last = ""
    for index, year in enumerate(years):
        spec = {"operator": operator, "year": year, "today": today}
        try:
            return year, client.login_check(make_call(spec)), ""
        except U8CoLoginFailed as exc:
            last = exc.message
            if index == 0 and len(years) > 1 and should_retry_year(exc.message):
                continue
            break
        except U8CoError as exc:
            return year, None, f"{exc.status} {exc.code} {exc.message}"
    return years[0], None, last


def check_bad_signature(today: str) -> tuple[str, str]:
    cfg = conf()
    fake = Operator(cfg.account, cfg.year, "demo", "invalid")
    client = U8CoClient(cfg.bridge_url, "11" * 32, timeout=30)
    spec = {"operator": fake, "year": cfg.year, "today": today}
    return _expect(lambda: client.login_check(make_call(spec)), 401, "unauthorized")


def check_foreign_account(client: U8CoClient, today: str) -> tuple[str, str]:
    cfg = conf()
    fake = Operator(cfg.account, cfg.year, "demo", "invalid")
    spec = {"operator": fake, "year": cfg.year, "today": today, "acc": cfg.foreign_probe}
    return _expect(lambda: client.login_check(make_call(spec)), 403, "account_not_allowed")


def _expect(action, status: int, code: str) -> tuple[str, str]:
    try:
        action()
    except U8CoError as exc:
        if exc.status == status and exc.code == code:
            return "PASS", code
        return "FAIL", f"{exc.status} {exc.code} {exc.message}"
    return "FAIL", "期望失败但调用成功"
