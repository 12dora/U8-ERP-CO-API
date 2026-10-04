"""凭证附件、单据附件列表路由（只读）。注册见 co_routes.register_co_routes。"""

from u8co_api.co_models_attach import GlAttachOut, VoucherAttachIn, VoucherAttachOut
from u8co_api.co_models_gl import GlKeyIn
from u8co_api.co_table import TAG_READ, CoRoute

ATTACH_ROUTES = (
    CoRoute(
        "/v1/co/gl/vouchers/attachments/list",
        "/v1/gl/vouchers/attachments/list",
        GlKeyIn,
        GlAttachOut,
        "总账凭证附件列表",
        "列出凭证上挂的电子附件（U8 凭证的「附件」）。\n\n"
        "**规则**\n"
        "- 只查数据库，不调用 U8 组件\n"
        "- 只列附件清单，不提供文件下载（文件在 U8 文件服务器上）\n"
        "- 凭证用 period、sign、no 定位，年度取登录日期的年份\n"
        "- 权限同读取凭证\n\n"
        "**错误**\n"
        "- 404 not_found：凭证不存在",
        "coGlVoucherAttachmentsList",
        "co:gl/vouchers/attachments/list",
        "总账凭证",
    ),
    CoRoute(
        "/v1/co/vouchers/attachments/list",
        "/v1/vouchers/attachments/list",
        VoucherAttachIn,
        VoucherAttachOut,
        "单据附件列表",
        "按类型和主键列出单据卡片上的附件（U8 通用附件表）。\n\n"
        "**规则**\n"
        "- 只查数据库，不调用 U8 组件。\n"
        "- 只列附件清单，不提供文件下载（文件在 U8 文件服务器上）。\n"
        "- 权限同读取该单据。\n\n"
        "**错误**\n"
        "- 单据不存在：404 not_found。\n"
        "- 越权：403 no_permission。",
        "coVoucherAttachmentsList",
        "co:vouchers/attachments/list",
        TAG_READ,
    ),
)
