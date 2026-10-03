"""凭证附件、单据附件列表路由（只读）。注册见 co_routes.register_co_routes。"""

from u8co_api.co_models_attach import GlAttachOut, VoucherAttachIn, VoucherAttachOut
from u8co_api.co_models_gl import GlKeyIn
from u8co_api.co_table import TAG_READ, CoRoute

_READ = "只读，只查数据库，不调用 U8 组件。只列附件清单，不提供文件下载（文件在 U8 文件服务器上）。"

ATTACH_ROUTES = (
    CoRoute(
        "/v1/co/gl/vouchers/attachments/list",
        "/v1/gl/vouchers/attachments/list",
        GlKeyIn,
        GlAttachOut,
        "总账凭证附件列表",
        _READ + "凭证用 period、sign、no 定位，年度取登录日期的年份。列出凭证上挂的电子附件（U8 凭证的「附件」），"
        "voucher.attachments 是附单据数（张数），两者不是一回事。权限同读取凭证；凭证不存在返回 404 not_found。",
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
        _READ + "按类型和主键列出单据卡片上的附件（U8 通用附件表）。权限同读取该单据；单据不存在返回 404 not_found，"
        "越权返回 403 no_permission。",
        "coVoucherAttachmentsList",
        "co:vouchers/attachments/list",
        TAG_READ,
    ),
)
