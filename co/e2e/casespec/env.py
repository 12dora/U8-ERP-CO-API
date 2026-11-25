"""一次回归里的客户端、挑单结果和本用例新建的单据。"""

from __future__ import annotations

from dataclasses import dataclass, field

from co.client.u8co_client import U8CoClient
from co.e2e.cases import Operator
from co.e2e.context import Run


@dataclass
class Env:
    ctx: Run
    client: U8CoClient
    operator: Operator
    today: str
    year: str
    audit_since: str
    bag: dict[str, str] = field(default_factory=dict)
    made: dict[str, str] = field(default_factory=dict)
    operator_name: str = ""
    focus: str = ""
    last_code: str = ""
    sql_n: int = 0

    def __repr__(self) -> str:
        return "Env()"
