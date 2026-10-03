"""修改（vouchers/update）的响应模型：通用的 CoEditOut 加生产订单修改专有的 allocates、details、changed。

单独成模块，免得生单响应（CoGenerateOut 继承 CoEditOut）也带上生产订单修改的字段。
"""

from __future__ import annotations

from u8co_api.co_models_edit import CoEditOut
from u8co_api.co_models_mo_update import CoMoUpdateOut


class CoUpdateOut(CoEditOut, CoMoUpdateOut):
    """修改响应。生产订单另有 allocates、details、changed，其它类型省略。"""
