"""有效权限（perm/snapshot、perm/evaluate）的请求、响应模型。

桥把过滤数据时用的同一份权限上下文序列化出来（单一来源）：功能权限、各档案的数据权限、是否账套主管和指纹。
data 的每一项是 {"all": true}（不受控）、{"codes": [...]}（受控；空数组表示什么都看不到）或 fitem 的 {"pairs": [...]}。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from u8co_api.co_models import CoAuth, _operator_ok

_PASS = ConfigDict(extra="allow")

SNAPSHOT_SUMMARY = "当前操作员的有效权限"
SNAPSHOT_HELP = (
    "只读。返回登录操作员自己在该账套、年度的有效权限：是否账套主管、功能权限编码、启用了数据权限控制的对象、"
    "各对象的数据权限（all 为不受控；codes 为受控时可见的编码，空数组表示一条都看不到；fitem 是项目大类与编码的 pairs）"
    "和指纹（权限不变时指纹不变，可作缓存键）。与桥过滤读接口时用的是同一份权限，桥上缓存 ttl_s 秒。"
    "不含口令、名称和金额。"
)
EVALUATE_SUMMARY = "查询指定操作员的有效权限"
EVALUATE_HELP = (
    "只读。用登录操作员的身份查询 subject 操作员的有效权限（字段同 perm/snapshot，另带 subject），不需要 subject 的口令。"
    "只有信任项写了 perm_evaluate: true 的调用方能调用；桥上还要求登录操作员在 permEvaluateOperators 名单里"
    "（账套主管也不例外），否则 403。subject 不存在返回 404。"
)


class PermEvaluateIn(CoAuth):
    subject: str = Field(
        ...,
        min_length=1,
        max_length=20,
        description="要查询的 U8 操作员编码，1 到 20 个字符，不含空白、引号或分号",
        examples=["op002"],
    )

    @field_validator("subject")
    @classmethod
    def _subject(cls, value: str) -> str:
        return _operator_ok(value)

    def audit_ref(self) -> str:
        return self.subject


class PermSnapshotOut(BaseModel):
    model_config = _PASS
    ok: bool = Field(description="总是 true")
    acc: str | None = Field(None, description="账套号")
    year: int | None = Field(None, description="登录年度")
    acct_year: int | None = Field(None, description="权限所取的账套年度")
    operator: str | None = Field(None, description="权限所属的操作员编码（perm/evaluate 时是 subject）")
    supervisor: bool | None = Field(None, description="是否账套主管（功能、数据权限都不受限）")
    gl_subj_ctl: bool | None = Field(None, description="是否启用了科目数据权限控制")
    roles: list[str] | None = Field(None, description="所属角色")
    functions: list[str] | None = Field(None, description="功能权限编码")
    objects_on: list[str] | None = Field(None, description="启用了数据权限控制的对象")
    data_admin: list[str] | None = Field(None, description="作为数据权限管理员不受限的对象")
    data: dict[str, Any] | None = Field(None, description="各对象的数据权限：all、codes（可带 live）或 pairs")
    ttl_s: int | None = Field(None, description="桥上权限缓存的秒数")
    fingerprint: str | None = Field(None, description="权限指纹（SHA-256 十六进制），权限不变时不变")


class PermEvaluateOut(PermSnapshotOut):
    subject: str | None = Field(None, description="被查询的操作员编码")
