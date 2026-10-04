"""写操作预演（dry_run）：请求体字段和预演响应的模型。

每个写路由的请求体都收 dry_run（旧路由 sale-orders/verify、dispatches/verify 除外；
arap/writeoff/auto 的 dry_run 仍是原来的「只算计划」）。为 true 时桥照常加锁、校验，
rollback 模式在事务里真实执行后回滚，validate 模式在 U8 自己提交的组件之前停下，都不写入。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

DRY_RUN_HELP = (
    "true 时只预演，不写入（照常登录、加锁、校验）。\n\n"
    "- 成功返回 DryRunOut（模式见其 mode），失败返回与正式写入相同的错误\n"
    "- 不能同时带 Idempotency-Key"
)

DryRunFlag = Annotated[StrictBool, Field(description=DRY_RUN_HELP)]

_PASS = ConfigDict(extra="allow")


class DryRunDoc(BaseModel):
    """预演时事务里的一张单据映像（回滚前在同一连接上读出）。"""

    model_config = _PASS
    type: str | None = Field(None, description="单据类型")
    id: int | str | None = Field(None, description="单据主键")
    code: str | None = Field(None, description="单据号")
    state: str | None = Field(None, description="exists（新增、修改、审核等之后仍在）或 deleted（事务里已删除）")
    head: dict[str, Any] | None = Field(None, description="表头：列名小写，空值省略；deleted 时省略")
    lines: list[dict[str, Any]] | None = Field(None, description="明细，最多 200 行；deleted 时省略")
    lines_total: int | None = Field(None, description="明细的实际行数")


class DryRunOut(BaseModel):
    """预演成功的响应（HTTP 200）。什么都没有写入。"""

    model_config = _PASS
    ok: bool = Field(description="总是 true")
    dry_run: Literal[True] = Field(description="总是 true，表示这是预演结果")
    mode: str = Field(
        description="rollback：真实执行后回滚（U8 自己的校验、编号、金额都跑过）；validate：只做了 U8 组件之前的检查"
    )
    route: str | None = Field(None, description="桥路由，如 vouchers/create")
    type: str | None = Field(None, description="单据类型")
    action: str | None = Field(None, description="操作")
    docs: list[DryRunDoc] | None = Field(None, description="受影响的单据映像，最多 10 张")
    detail: dict[str, Any] | None = Field(None, description="各操作自己的补充信息")
    warnings: list[str] | None = Field(
        None,
        description="提醒码：number_may_skip（预演占用的单号 U8 可能不退回）、validate_only（U8 保存时的检查没有跑）、"
        "locks_held（预演期间持有与正式写入相同的锁）",
    )
    message: str | None = Field(None, description="中文说明")
