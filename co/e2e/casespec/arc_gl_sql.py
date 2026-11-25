"""总账档案写入用例（arc_gl）的挑选 SQL。标识符是常量；挑出来的值只收字母数字（SQL 里过滤，Python 再核一遍）。

每条 SQL 输出一行 ROW:<值>[|<值>]，挑不到就没有 ROW 行（用例记 N/A）。SQL 已实测。
"""

from __future__ import annotations

import re

# 凭证类别字最长 2；币种名称最长 8、符号最长 4。
_SIGNS = [f"Z{c}" for c in "123456789ABCDEFGHJKLMNPQRSTUVWXY"]
_NAMES = [f"ZZE2E{n}" for n in range(10)]
_SYMBOLS = [f"Z{c}" for c in "123456789ABCDEFGH"]
# 桥拒绝删除的预置类别字。
PRESET_SIGNS = frozenset(("收", "付", "转", "记"))
_SIGN = re.compile(r"^[一-鿿]{1,2}\Z")


def _values(items: list[str]) -> str:
    return ", ".join(f"({i}, N'{code}')" for i, code in enumerate(items))


def sign_free() -> str:
    # 空闲的凭证类别字（不分大小写，与库的排序规则一致）。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + c.code AS v
FROM (VALUES {_values(_SIGNS)}) AS c(n, code)
WHERE NOT EXISTS (SELECT 1 FROM dbo.dsign AS d WHERE d.csign = c.code)
ORDER BY c.n;
"""


def sign_preset(sign: str) -> str:
    # 预置类别（测试数据的转账类别字）在账套里（删除闸门用；桥对 收 / 付 / 转 / 记 一律 409）。只输出 1，不输出类别字本身。
    if sign not in PRESET_SIGNS or _SIGN.fullmatch(sign) is None:
        raise ValueError("不是预置凭证类别")
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:1' AS v FROM dbo.dsign WHERE csign = N'{sign}';
"""


def currency_free() -> str:
    # 空闲的币种名称 | 空闲的币种符号。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + c.name + N'|' + s.sym AS v
FROM (VALUES {_values(_NAMES)}) AS c(n, name)
CROSS JOIN (SELECT TOP (1) t.sym FROM (VALUES {_values(_SYMBOLS)}) AS t(n, sym)
            WHERE NOT EXISTS (SELECT 1 FROM dbo.foreigncurrency AS f WHERE f.cexch_code = t.sym) ORDER BY t.n) AS s
WHERE NOT EXISTS (SELECT 1 FROM dbo.foreigncurrency AS f WHERE f.cexch_name = c.name)
ORDER BY c.n;
"""
