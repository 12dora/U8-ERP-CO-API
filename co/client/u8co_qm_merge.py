"""产品检验单合并检验（vouchers/generate，type=qm_product_check，正文 source_ids 代替 id）。混入 U8CoClient。

source_ids 是 2 到 20 个已审核产品报检单的表体行 id（AUTOID，不是报检单 ID）（可来自不同报检单，不重复）；head 同单行检验单生单
（cCheckPersonCode 必填，可带 items）。lines 可省略（每条来源检验整行数量、全部合格）；给出时与 source_ids 一一对应，
每行可带 quantity、fRegQuantity、fConQuantiy、fDisQuantity。同存货 / 单位 / 批号 / 检验方式 / 检验类型等由桥查库核对。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_gl_arc import _common
from co.client.u8co_idem import with_key
from co.client.u8co_qm import qm_head
from co.client.u8co_rows import _present_row, _row

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

KIND = "qm_product_check"
_ID_MAX = 2147483647
_MIN, _MAX = 2, 20
_FILE_KEYS = frozenset({"head", "source_ids", "lines", "merge_rules"})


@dataclass(frozen=True)
class MergedCheck:
    """source_ids：报检单表体 AUTOID 列表；head、lines 同模块说明。"""

    source_ids: Any
    head: Any = None
    lines: Any = None
    # U8 合并规则编码（缺省由服务端取 ["0"] 自由项）；1 到 9 个整数或数字串。
    rules: Any = None


def merged_body(call: U8Call, draft: MergedCheck) -> dict[str, Any]:
    body = _common(call)
    body["type"] = KIND
    ids = _ids(draft.source_ids)
    body["source_ids"] = ids
    head = qm_head(KIND, draft.head, _present_row)
    if head is not None:
        body["head"] = head
    if draft.lines is not None:
        if not isinstance(draft.lines, (list, tuple)) or len(draft.lines) != len(ids):
            raise ValueError("合并检验的 lines 可省略；给出时必须与 source_ids 一一对应")
        body["lines"] = [_row(item) for item in draft.lines]
    if draft.rules is not None:
        body["merge_rules"] = _rules(draft.rules)
    return body


def _rules(raw: object) -> list[Any]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 1:
        raise ValueError("merge_rules 只能给一个合并规则编码")
    for one in raw:
        if not (type(one) is int or (type(one) is str and one.isdigit())):
            raise ValueError("merge_rules 的每一项必须是整数或数字串")
    return list(raw)


def _ids(raw: object) -> list[int]:
    if not isinstance(raw, (list, tuple)) or not _MIN <= len(raw) <= _MAX:
        raise ValueError(f"source_ids 必须是 {_MIN} 到 {_MAX} 条报检单表体行")
    ids: list[int] = []
    for one in raw:
        if type(one) is not int or not 1 <= one <= _ID_MAX:
            raise ValueError("source_ids 的每一项必须是正整数")
        if one in ids:
            raise ValueError("来源报检单行重复")
        ids.append(one)
    return ids


def cli_merged(data: dict[str, Any], doc_id: int | None, kind: str, source_type: str) -> MergedCheck:
    """CLI 的 generate 文件里写了 source_ids：只用于 --type qm_product_check、不带 --id，文件只能有 head、source_ids、lines。"""
    if doc_id is not None or kind != KIND or not set(data) <= _FILE_KEYS or source_type not in ("", "qm_product_inspect"):
        raise SystemExit("source_ids 只用于 --type qm_product_check、不带 --id，文件只能包含 head、source_ids 和 lines")
    return MergedCheck(data["source_ids"], data.get("head"), data.get("lines"), data.get("merge_rules"))


class U8CoQmMergeMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def generate_merged_check(
        self, call: U8Call, draft: MergedCheck, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """产品检验单合并检验；call.doc_id 不用（来源在 source_ids 里）。响应另有 source_ids。"""
        return self.call("/v1/vouchers/generate", with_key(merged_body(call, draft), idempotency_key))
