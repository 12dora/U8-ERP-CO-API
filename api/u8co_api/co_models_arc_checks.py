"""archives/create、update 的 fields 格式校验的分派：各类档案的检查（桥各档案的登录前校验）按固定顺序排在一起。"""

from __future__ import annotations

from typing import Any

from u8co_api.co_fa_card_write import check_fa_write
from u8co_api.co_models_arc_partner import check_partner_write
from u8co_api.co_models_arc_rw import check_code_tag, check_exch_write, check_gl_write, is_gl_archive
from u8co_api.co_models_arc_sa import check_sa_write

_PROJECT = "project"
_PROJECT_TAGS = frozenset({"name", "bclose", "citemccode"})


def check_project_write(archive: str, fields: dict[str, Any], template: str | None, creating: bool) -> None:
    """project 只收 name、bclose、citemccode，新增必须有 citemccode，不收 template（桥 ArcProjectWrite.Check）。"""
    if archive != _PROJECT:
        return
    unknown = sorted(set(fields) - _PROJECT_TAGS)
    if unknown:
        raise ValueError("project 只能写 name、bclose、citemccode，未知字段 " + ", ".join(unknown))
    if template is not None:
        raise ValueError("项目档案不支持 template")
    if creating and "citemccode" not in fields:
        raise ValueError("新增项目必须给 citemccode（所属末级分类）")
    close = fields.get("bclose")
    if close is not None and close not in (True, False, 0, 1, "0", "1"):
        raise ValueError("bclose 必须是布尔或 0 / 1")


def check_create_fields(archive: str, code: str, fields: dict[str, Any], template: str | None, keyed: bool) -> None:
    """新增：keyed 为 true 的档案（两列主键、汇率）不收 template，总账档案也不收。"""
    check_code_tag(archive, fields, True)
    check_gl_write(archive, code, fields, True)
    check_project_write(archive, fields, template, True)
    check_sa_write(archive, fields, template, True)
    check_partner_write(archive, code, fields, True)
    check_exch_write(archive, code, fields, True)
    check_fa_write(archive, fields, template)  # 固定资产卡片、设备台账
    if template is not None and (keyed or is_gl_archive(archive)):
        raise ValueError(f"档案 {archive} 不支持 template")


def check_update_fields(archive: str, code: str, fields: dict[str, Any]) -> None:
    check_code_tag(archive, fields, False)
    check_gl_write(archive, code, fields, False)
    check_project_write(archive, fields, None, False)
    check_sa_write(archive, fields, None, False)
    check_partner_write(archive, code, fields, False)
    check_exch_write(archive, code, fields, False)
