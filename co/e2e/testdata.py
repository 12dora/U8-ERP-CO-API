"""测试账套里的档案编码（科目、部门、人员、收发类别、存货模板……）。

这些值每个站点都不一样，放在 e2e 配置 testdata.file 指向的 JSON 里，样例见 co/e2e/testdata.example.json。
文件不存在或缺某个键时，用到它的用例记 SKIP，不去猜。能用 SQL 现挑的值仍在 casespec 里现挑。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_TEXT = re.compile(r"^[^\x00-\x1f'\"\\;]{1,60}\Z")
_EXAMPLE = "co/e2e/testdata.example.json"

# 生产单据的收发类别：材料出库、产成品入库生单时桥要求表头带 crdcode（没有缺省值），用例从这两个键取。
MATERIAL_OUT_RD = "stock.material_out_rd"
PRODUCT_IN_RD = "stock.product_in_rd"

# 总账凭证类别字：转账类凭证、银行 / 现金（带现金流量）凭证各用哪个。可选键，没配用 U8 常见的「转」「付」；
# 只设一种类别「记」的账套两个都写「记」。
SIGN_TRANSFER = "gl.sign_transfer"
SIGN_BANK = "gl.sign_bank"
DEFAULT_TRANSFER_SIGN = "转"
_SIGN_DEFAULTS = {SIGN_TRANSFER: DEFAULT_TRANSFER_SIGN, SIGN_BANK: "付"}
# 销售发票上记数电发票号的配置列（如 cDefine10），可选。
TAX_INVOICE_NO_FIELD = "taxno.tax_invoice_no_field"
# 与 gl_voucher_sql 的凭证键白名单一致：1～2 个汉字，可直接拼进 N'…'。
_SIGN = re.compile(r"^[一-鿿]{1,2}\Z")


class DataMissing(KeyError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key

    def __str__(self) -> str:
        return self.reason()

    def reason(self) -> str:
        return f"测试数据未配置 {self.key}（见 {_EXAMPLE}）"


def load_file(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"读不了测试数据 {path}: {type(exc).__name__}") from None
    if not isinstance(data, dict):
        raise RuntimeError("测试数据必须是 JSON 对象")
    return data


_CURRENT: list[dict] = []


def current() -> dict:
    if not _CURRENT:
        from co.e2e.settings import conf

        _CURRENT.append(load_file(Path(conf().testdata_file)))
    return _CURRENT[0]


def use(data: dict | None) -> None:
    # 单元测试装一份内存数据；传 None 清掉，下次按配置重新读文件。
    _CURRENT.clear()
    if data is not None:
        _CURRENT.append(data)


def _lookup(key: str) -> object:
    return _walk(key)


def _walk(key: str) -> object:
    node: object = current()
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise DataMissing(key)
        node = node[part]
    return node


def _clean(key: str, value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise DataMissing(key)
    text = str(value).strip()
    if _TEXT.fullmatch(text) is None:
        raise DataMissing(key)
    return text


def value(key: str) -> str:
    try:
        raw = _lookup(key)
    except DataMissing:
        if key not in _SIGN_DEFAULTS:
            raise
        raw = _SIGN_DEFAULTS[key]
    text = _clean(key, raw)
    if key in _SIGN_DEFAULTS and _SIGN.fullmatch(text) is None:
        raise DataMissing(key)
    return text


def gl_sign(key: str) -> str:
    """凭证类别字（SIGN_TRANSFER / SIGN_BANK）：没配用缺省，配了但不合法抛 DataMissing。"""
    if key not in _SIGN_DEFAULTS:
        raise ValueError("不是凭证类别键")
    return value(key)


def values(key: str) -> tuple[str, ...]:
    raw = _lookup(key)
    if not isinstance(raw, list) or not raw:
        raise DataMissing(key)
    return tuple(_clean(key, item) for item in raw)


def first_missing(keys: tuple[str, ...]) -> str:
    for key in keys:
        try:
            if key.endswith("codes"):
                values(key)
            else:
                value(key)
        except DataMissing:
            return key
    return ""
