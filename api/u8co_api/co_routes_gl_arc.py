"""/v1/co 路由：总账凭证、基础档案、单据列表和现存量。注册见 co_routes.register_co_routes。"""

from u8co_api.co_models_arc import (
    ArcCreateIn,
    ArcGetIn,
    ArcGetOut,
    ArcKeyIn,
    ArcListIn,
    ArcListOut,
    ArcUpdateIn,
    ArcWriteOut,
)
from u8co_api.co_models_gl_digest import GL_DIGEST_HELP, GlDigestIn, GlDigestOut  # 凭证摘要（事件源）
from u8co_api.co_models_gl_post import GL_POST_HELP, GlPostIn, GlPostOut
from u8co_api.co_models_gl_reverse import GL_REVERSE_HELP, GlReverseIn, GlReverseOut  # 红字冲销
from u8co_api.co_models_gl_unpost import GL_UNPOST_HELP, GlUnpostIn, GlUnpostOut  # 取消记账
from u8co_api.co_models_list import CoStockIn, CoStockOut, CoVoucherListIn, CoVoucherListOut
from u8co_api.co_models_gl import (
    GlCreateIn,
    GlKeyActIn,
    GlKeyIn,
    GlKeyOut,
    GlListIn,
    GlListOut,
    GlLoadOut,
    GlUpdateIn,
)
from u8co_api.co_doctext import op_doc
from u8co_api.co_table import TAG_READ, CoRoute

TAG_GL = "总账凭证"
TAG_ARC = "基础档案"
TAG_STOCK = "现存量"

_READ = "只读。"
_GL_KEY = "- 凭证用 period、sign、no 定位\n- 只处理总账手工凭证，其它模块生成的凭证返回 409\n"
_GL_SELF = "\n\n**错误**\n- 504 outcome_unknown：U8 保存后已自己提交，先 load 或 list 核对再重试"
_ARC_UNKNOWN = "504 outcome_unknown：U8 自己提交，结果不明；先 get 核对再重试"


def _gl(op: str, out: type, summary: str, text: str, body: type = GlKeyActIn) -> CoRoute:
    words = op.title().replace("_", "")
    return CoRoute(
        f"/v1/co/gl/vouchers/{op}",
        f"/v1/gl/vouchers/{op}",
        body,
        out,
        summary,
        text,
        f"coGlVoucher{words}",
        f"co:gl/vouchers/{op}",
        TAG_GL,
    )


def _arc(op: str, pair: tuple[type, type], summary: str, text: str) -> CoRoute:
    return CoRoute(
        f"/v1/co/archives/{op}",
        f"/v1/archives/{op}",
        pair[0],
        pair[1],
        summary,
        text,
        f"coArchive{op.title()}",
        f"co:archives/{op}",
        TAG_ARC,
    )


def _gl_key(first: str, *rules: str) -> str:
    # 按凭证号定位的单张操作：一句话说明加规则。
    items = "\n".join(f"- {rule}" for rule in rules)
    return f"{first}\n\n**规则**\n{_GL_KEY}{items}"


_GL_ROUTES = (
    _gl("load", GlLoadOut, "读取总账凭证", "读取一张凭证的表头、状态和全部分录（含辅助核算和现金流量）。", GlKeyIn),
    _gl(
        "list",
        GlListOut,
        "总账凭证列表",
        "按期间范围列出凭证，可按类别、日期、制单人和状态过滤。",
        GlListIn,
    ),
    # 凭证摘要（桥 GlDigest，事件源）。
    _gl("digest", GlDigestOut, "总账凭证摘要", GL_DIGEST_HELP, GlDigestIn),
    _gl(
        "create",
        GlKeyOut,
        "新增总账凭证",
        "通过 U8 凭证导入新增手工凭证。\n\n"
        "**规则**\n"
        "- 凭证号由 U8 按期间和类别自动编号\n"
        "- 期间取 head.date 的月份\n"
        "- head.date 的年份必须等于登录日期（date）的年份；year 是账套库年度，不参与判断" + _GL_SELF,
        GlCreateIn,
    ),
    _gl(
        "update",
        GlKeyOut,
        "修改总账凭证",
        _gl_key("整张替换凭证的表头和分录，凭证号不变。", "只改未审核、未出纳签字、未作废、未记账的凭证") + _GL_SELF,
        GlUpdateIn,
    ),
    _gl("void", GlKeyOut, "作废总账凭证", _gl_key("作废一张凭证。", "只作废未审核、未签字、未记账的凭证")),
    _gl("unvoid", GlKeyOut, "取消作废", _gl_key("取消凭证的作废标志。", "只处理已作废的凭证")),
    _gl(
        "verify",
        GlKeyOut,
        "审核总账凭证",
        _gl_key("审核一张凭证。", "不能审核已作废或已审核的凭证", "账套不允许制单人审核时，本人制单返回 409"),
    ),
    _gl("unverify", GlKeyOut, "取消审核", _gl_key("取消凭证的审核。", "只处理已审核、未记账的凭证")),
    _gl("sign", GlKeyOut, "出纳签字", _gl_key("对凭证做出纳签字。", "只处理含现金或银行科目的凭证")),
    _gl("unsign", GlKeyOut, "取消出纳签字", _gl_key("取消凭证的出纳签字。", "只处理已签字、未记账的凭证")),
    _gl(
        "delete",
        GlKeyOut,
        "删除总账凭证",
        _gl_key("删除一张凭证，不重排凭证号。", "只删除已作废、未记账的凭证"),
    ),
    # 记账（桥 GlPost）。
    _gl("post", GlPostOut, "记账", GL_POST_HELP, GlPostIn),
    # 取消记账（桥 GlUnpost，测试账套）。
    _gl("unpost", GlUnpostOut, "取消记账", GL_UNPOST_HELP, GlUnpostIn),
    # 红字冲销（桥 GlReverse）。
    _gl("reverse", GlReverseOut, "红字冲销总账凭证", GL_REVERSE_HELP, GlReverseIn),
)

_ARC_ROUTES = (
    _arc(
        "get",
        (ArcGetIn, ArcGetOut),
        "读取档案",
        op_doc(
            "按编码读取一条档案。",
            (
                "规则",
                (
                    "可写档案按 EAI 标签名返回全部有值的标签（含银行账号、联系方式），只去掉口令类列",
                    "只读档案（科目、计量单位、结算方式、凭证类别、币种、项目、货位等，见 archive）按表列名返回",
                    "customer_address、user_define、customer_inventory 的编码写成 `<第一段>:<第二段>`",
                    "exchange_rate 的编码写成 `<币种>:<年度>:<期间>[:<日>]`",
                    "exchange_rate 写成 `<币种>:<yyyy-mm-dd>` 时，返回该日期单据会用的汇率",
                    "按日期取汇率同 U8：固定汇率取当月记账汇率，浮动汇率取当日汇率",
                ),
            ),
        ),
    ),
    _arc(
        "list",
        (ArcListIn, ArcListOut),
        "档案列表",
        op_doc(
            "按编码排序列出档案。",
            (
                "用法",
                (
                    "可按编码前缀 code_prefix、名称 name_like 和 changed_since 过滤",
                    "翻页：next 原样放进 after 读下一页",
                    "project 可用 project_class 只列一个项目大类",
                    "exchange_rate 可用 currency、fiscal_year（缺省登录年份）过滤",
                    "fa_card 可用 type_code、dept_code、include_disposed（缺省不含已减少）过滤",
                ),
            ),
            (
                "规则",
                (
                    "customer_address、user_define、customer_inventory 的编码是 `<第一段>:<第二段>`",
                    "上述两段编码的档案：code_prefix 按整串匹配，class_code 是第一段",
                    "exchange_rate 每行是一个币种一个期间（浮动汇率是一天）的 rate 和 adjust_rate",
                    "fa_card 取登录月末的卡片版本",
                ),
            ),
            (
                "限制",
                (
                    "没有 ufts、不支持 changed_since：voucher_sign、project、customer_address、fa_card、operator、role",
                    "客户、供应商银行账户（customer_bank、vendor_bank）也没有 ufts",
                ),
            ),
            (
                "权限",
                (
                    "operator 操作员、role 角色只列本账套有授权的，只有账套主管能读",
                    "aa_bank 银行档案也只有账套主管能读",
                ),
            ),
        ),
    ),
    _arc(
        "create",
        (ArcCreateIn, ArcWriteOut),
        "新增档案",
        op_doc(
            "通过 U8 EAI 新增一条档案（项目走受控 SQL）。",
            (
                "用法",
                (
                    "inventory 存货：必须给 template（已有存货编码）",
                    "customer、vendor：要给 tax_reg_code",
                    "bank 本单位开户银行：须给 name、account、cbankcode、ccurrencyname",
                    "position 货位须给 warehouse_code；unit 计量单位须给 group_code",
                    "customer_inventory 须给 ccusinvname；unit_group 须给 type；rd_style 一级须给 rsflag",
                    "currency 币种：编码是币种名称，fields 的 code 是币种符号（必填）",
                    "各档案的编码和 fields 见 archive、code 字段说明",
                ),
            ),
            (
                "规则",
                (
                    "走 U8 EAI：客户、供应商、存货、部门、人员、仓库和分类",
                    "也走 EAI：开户银行、货位、计量单位、自定义项档案（user_define）、客户存货对照",
                    "也走 EAI：计量单位组、结算方式、收发类别、采购类型、销售类型、地区分类、银行档案",
                    "project 走受控 SQL：fields 只收 name、bclose、citemccode（末级分类），桥自己提交后回读",
                    "user_define、customer_inventory 的编码写成 `<第一段>:<第二段>`，不收 template",
                    "分级的档案按编码方案校验上级",
                    "fa_card 固定资产卡片：走 U8 官方 EAI 导入原始卡片，code 是资产编号",
                    "fa_card 响应的 code 是 U8 编的卡片编号，另有 asset_num、card_id",
                    "fa_card：登录日期须在固定资产当前未结账的期间，开始使用日期早于该期间",
                    "equipment 设备台账：走 U8 官方 EAI 导入，只能新增",
                    "U8 自己提交",
                ),
            ),
            ("错误", (_ARC_UNKNOWN,)),
            ("相关", ("fa_card 新增后用 archives/get 读",)),
        ),
    ),
    _arc(
        "update",
        (ArcUpdateIn, ArcWriteOut),
        "修改档案",
        op_doc(
            "修改档案：只改 fields 里列出的标签。",
            (
                "规则",
                (
                    "不能改编码",
                    "project 只能改 name、bclose、citemccode",
                    "currency 不能改名称和币种符号",
                    "U8 自己提交",
                ),
            ),
            ("错误", (_ARC_UNKNOWN,)),
        ),
    ),
    _arc(
        "delete",
        (ArcKeyIn, ArcWriteOut),
        "删除档案",
        op_doc(
            "删除一条档案。",
            (
                "规则",
                (
                    "project 走受控 SQL，删除后回读确认",
                    "fa_card（code 是卡片编号）只撤销本期新增、只有一个版本（没有变动单、未减少）的卡片",
                    "撤销 fa_card 不是资产减少；资产减少请在 U8 客户端处理",
                    "U8 自己提交",
                ),
            ),
            (
                "错误",
                (
                    "409 u8_rejected：档案已被单据使用，U8 拒绝",
                    "409 state_mismatch：project 被凭证、单据或其他档案引用（桥先拒绝）",
                    "409 state_mismatch：货位有下级或存货记录，计量单位已被存货使用（桥先拒绝）",
                    "409 state_mismatch：计量单位组、结算方式、收发类别、采购类型、销售类型、币种、地区分类、"
                    "银行档案有下级、被主要档案或单据引用，或是本位币（桥先拒绝）",
                    "409 state_mismatch：fa_card 不是本期新增，或已有变动单、已减少",
                    _ARC_UNKNOWN,
                ),
            ),
        ),
    ),
)

_LIST_ROUTES = (
    CoRoute(
        "/v1/co/vouchers/list",
        "/v1/vouchers/list",
        CoVoucherListIn,
        CoVoucherListOut,
        "单据列表",
        "按主键列出某类单据的表头。\n\n"
        "**用法**\n"
        "- 可过滤、增量（changed_since）并按主键续读（after）。\n"
        "- keys_only 只返回 id、code、ufts，用来发现删除。",
        "coVoucherList",
        "co:vouchers/list",
        TAG_READ,
    ),
    CoRoute(
        "/v1/co/stock/current",
        "/v1/stock/current",
        CoStockIn,
        CoStockOut,
        "现存量",
        "按 CurrentStock 主键列出现存量。\n\n"
        "**用法**\n"
        "- 可按仓库、存货、批号过滤。\n"
        "- qty_available 的算法见响应说明。",
        "coStockCurrent",
        "co:stock/current",
        TAG_STOCK,
    ),
)

GL_ARC_ROUTES = _GL_ROUTES + _ARC_ROUTES + _LIST_ROUTES
