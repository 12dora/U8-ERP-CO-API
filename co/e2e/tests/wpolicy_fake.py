"""写入策略用例的回放替身：同一个对象既当远端（run_body 读写策略文件）又当桥客户端（health、call）。不连接服务器。"""

from __future__ import annotations

import base64
import json
import re
from types import SimpleNamespace

from co.client.u8co_client import U8CoError

PERMISSIVE_BYTES = b'{"version":1,"unlisted":"allow"}'
_B64 = re.compile(r"FromBase64String\('([^']*)'\)")


def error(status: int, code: str, message: str = "", field: str = "", detail: dict | None = None) -> U8CoError:
    exc = U8CoError(status, code, message, field)
    exc.detail = detail
    return exc


class FakeBridge:
    """写入的内容是合法 JSON 且没有 e2e 开头的键就当重载成功（loaded_at 换一个），否则 state 变 invalid、保留上一份。
    与真桥一样按内容判断变化：写入与上次读到的内容逐字节相同时不重载（state、loaded_at 都不变）。"""

    def __init__(
        self, answers: list | None = None, original: bytes | None = PERMISSIVE_BYTES, state: str = "ok"
    ) -> None:
        self.answers = list(answers or [])
        self.content = original
        self.seen = original
        self.snap = json.loads(original) if original else {}
        self.state = state
        self.loaded = 0
        self.with_policy = True
        self.signatures = ["ok"]
        self.calls: list[tuple[str, dict]] = []
        self.log: list[tuple[str, object]] = []

    # 远端
    def run_body(self, body: str, _timeout: int) -> SimpleNamespace:
        if "'read' -eq 'read'" in body:
            out = "WPF-NONE" if self.content is None else "WPF-B64:" + base64.b64encode(self.content).decode("ascii")
            return SimpleNamespace(code=0, out=out + "\n")
        found = _B64.search(body)
        assert found is not None
        self._load(base64.b64decode(found.group(1)))
        return SimpleNamespace(code=0, out="WPF-WRITTEN\n")

    def _load(self, data: bytes) -> None:
        self.content = data
        self.log.append(("write", data))
        if data == self.seen:
            return
        self.seen = data
        try:
            snap = json.loads(data)
        except ValueError:
            self.state = "invalid"
            return
        if any(key.startswith("e2e") for key in snap):
            self.state = "invalid"
            return
        self.snap = snap
        self.state = "ok"
        self.loaded += 1

    def written(self) -> list[dict | str]:
        out: list[dict | str] = []
        for kind, data in self.log:
            if kind == "write":
                try:
                    out.append(json.loads(data))  # type: ignore[arg-type]
                except ValueError:
                    out.append(str(data))
        return out

    # 桥客户端
    def health(self) -> dict:
        sig = self.signatures.pop(0) if len(self.signatures) > 1 else self.signatures[0]
        payload: dict = {"ok": True, "version": "1", "signatures": sig}
        if not self.with_policy:
            return payload
        freeze = self.snap.get("freeze") or {}
        payload["write_policy"] = {
            "state": self.state,
            "version": self.snap.get("version"),
            "loaded_at": f"2026-10-03T08:00:{self.loaded:02d}Z",
            "freeze": {"global": bool(freeze.get("global")), "accounts": list(freeze.get("accounts") or [])},
            "window_open": not self.snap.get("denyDates"),
        }
        return payload

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        self.log.append(("call", route))
        got = self.answers.pop(0) if self.answers else {"ok": True}
        if isinstance(got, U8CoError):
            raise got
        return got


def env_for(bridge: FakeBridge, bag: dict | None = None) -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="op001", password="pw")
    return SimpleNamespace(
        client=bridge,
        ctx=SimpleNamespace(remote=bridge),
        operator=operator,
        year="2026",
        today="2026-10-03",
        bag=dict(bag or {}),
    )
