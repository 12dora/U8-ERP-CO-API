"""客户、供应商的子档案（桥 ArcPartner）：银行账户、联系人。

编码是两段 <客户或供应商编码>:<银行账号或联系人编码>，一次只写一行；fields 的标签和格式以 API、桥为准：
银行账户 branch（新增必填）、bank_code、account_name、default 等；联系人 name（新增必填）、sex、marriage、mobile 等
（供应商联系人没有 position、favorite）。联系人由 U8 自动编号：新增的 code 写成 "<客户或供应商编码>:"，
响应的 code 是 U8 编的号。供应商联系人可写（新增走 EAI，修改、删除由桥直接改表）。
"""

from __future__ import annotations

PARTNER_ARCHIVES = ("customer_bank", "vendor_bank", "customer_contact", "vendor_contact")
# 可新增、修改、删除的（四类都可写）。
PARTNER_WRITE_ARCHIVES = PARTNER_ARCHIVES
# 编码整串的最大长度：客户（供应商）编码 20 + ":" + 账号 50 或联系人编码 30。
PARTNER_CODE_LIMIT = {"customer_bank": 71, "vendor_bank": 71, "customer_contact": 51, "vendor_contact": 51}
