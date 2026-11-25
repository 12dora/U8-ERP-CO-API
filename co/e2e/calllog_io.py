"""从服务器拉当天审计。口令不会写进报告。"""

from __future__ import annotations

from co.e2e.cases import parse_audit
from co.e2e.context import Run
from co.e2e.settings import conf


def pull_audit(ctx: Run, day: str) -> list[dict]:
    result = ctx.remote.run_script("read-audit.ps1", {"Day": day}, 60)
    if result.code != 0:
        raise RuntimeError("读取审计失败")
    remote = conf().remote("out", "audit-" + day + ".log")
    local = ctx.report_dir / ("audit-" + day + ".log")
    ctx.remote.download(remote, local)
    text = ctx.scrub(local.read_text(encoding="utf-8-sig"))
    local.write_text(text, encoding="utf-8")
    return parse_audit(text)
