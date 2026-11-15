"""基础档案 /v1/co/archives/* 各操作收的档案名（Literal）和说明文字。"""

from __future__ import annotations

from typing import Literal

from u8co_api.co_doctext import field_doc, section
from u8co_api.co_fa_card_write import FA_RO_DESC, FA_WRITE_DESC
from u8co_api.co_models_arc_fa import FA_RO
from u8co_api.co_models_arc_partner import PARTNER_RO_DESC, PARTNER_WRITE_DESC

# U8 操作员、角色（桥 ArcUa，系统库 UFSYSTEM）：只列本账套有授权的，编码最长 20，没有 ufts；只认账套主管（银行档案 aa_bank 同样只认主管）。
UA_RO = (
    "`operator` U8 操作员：编码是操作员编码；返回姓名、部门、停用标志、所属角色 roles、关联人员 person_code，不含口令",
    "`role` 角色：返回成员 members",
)
UA_PERM = (
    "operator、role 只列在本账套有授权的，只有账套主管能读",
    "aa_bank 银行档案也只有账套主管能读",
)

ARCHIVE_DESC = field_doc(
    "档案名。",
    (
        "",
        (
            "`customer` 客户、`vendor` 供应商、`inventory` 存货、`department` 部门、`person` 人员、`warehouse` 仓库",
            "`customer_class` 客户分类、`vendor_class` 供应商分类、`inventory_class` 存货分类、`reason` 原因码",
        ),
    ),
)
ArchiveName = Literal[
    "customer",
    "vendor",
    "inventory",
    "department",
    "person",
    "warehouse",
    "customer_class",
    "vendor_class",
    "inventory_class",
]
# 开户银行可新增、修改、删除（EAI）；项目走受控 SQL，fields 只收 name、bclose、citemccode，删除只删没有被引用的项目。
# 货位、计量单位可新增、修改、删除（EAI）；自定义项档案、客户存货对照只能新增、删除（U8 不提供修改）。
# 两列主键的档案不收 template。
# 之后：计量单位组、结算方式、收发类别、采购类型、销售类型、地区分类、银行档案可新增、修改、删除（EAI）。
# 币种、凭证类别可新增（EAI，U8PzInsert 的组件；桥 ArcGl）、修改、删除（受控 SQL）；会计科目仍只读。
# 汇率、供应商联系人可新增（EAI 分发器；桥 ArcExchWrite、ArcVenContact）、修改、删除（受控 SQL）。
# 原因码可新增、修改、删除（EAI，桥 ArcReason），get、list 与九类档案一样按 EAI 标签返回。
_WRITE_SIMPLE = (
    "`bank` 本单位开户银行：编码最长 3",
    "`project` 项目：编码 `<项目大类>:<项目编码>`；已被单据、凭证等引用的不能删除",
    "`position` 货位：编码最长 20，warehouse_code 必填",
    "`unit` 计量单位：编码最长 35，group_code 必填",
    "`unit_group` 计量单位组：type 必填（0 无换算、1 固定换算、2 浮动换算）",
    "`user_define` 自定义项档案：编码 `<自定义项号>:<档案值>`",
    "`customer_inventory` 客户存货对照：编码 `<客户编码>:<存货编码>`，ccusinvname 必填",
    "`settle_style` 结算方式：编码最长 3，按编码方案分级",
    "`rd_style` 收发类别：编码最长 5，按编码方案分级，一级须给 rsflag",
    "`purchase_type` 采购类型、`sale_type` 销售类型：编码最长 2",
    "采购类型、销售类型：U8 档案设置可能要求 rstype_code 入（出）库类别必输",
    "`district_class` 地区分类：编码最长 12，按编码方案分级",
    "`aa_bank` 银行档案：编码最长 5",
)
_WRITE_DETAIL = (
    (
        "currency 币种",
        (
            "编码是币种名称，最长 8",
            "fields 的 code 是币种符号：新增必填，不能修改",
            "本位币不能写",
            "已被单据、档案引用的不能修改、删除；删除时连同汇率一起删",
        ),
    ),
    (
        "voucher_sign 凭证类别",
        (
            "编码是类别字，最长 2",
            "新增必须给 type_name；修改只收 type_name",
            "预置的收付转记、最后一个和已被凭证等引用的不能删除",
        ),
    ),
    (
        "exchange_rate 汇率",
        (
            "`<币种>:<年度>:<期间>` 是固定汇率：fields 收 rate 记账汇率、adjust_rate 调整汇率",
            "`<币种>:<年度>:<期间>:<日>` 是浮动汇率：只收 rate，新增时日写成 yyyy-mm-dd",
            "新增走 U8 的 EAI 导入：年度必须是登录年度，一次只写一种汇率，已存在的用 update",
            "修改、删除由桥直接改表；删除删掉该编码下的全部汇率",
            "本位币、总账已结账的期间不能写",
        ),
    ),
    (
        "reason 原因码",
        (
            "编码最长 10",
            "fields 收 name 名称（最长 30）、ReasonMemo 说明（最长 240）",
            "Reasontype 所属类型：新增必填，0 到 255 的整数",
            "Reasontype 取值：1 不良品原因、2 让步放行原因、3 采购退货原因、4 销售退货原因、5 变更原因、6 拖欠原因",
            "Reasontype 的其他取值按 U8 原因码分类",
            "已被不良品处理单、检验单、发货单、销售发票等引用的不能删除",
        ),
    ),
)
# 开户银行可新增、修改、删除（EAI）；项目走受控 SQL，fields 只收 name、bclose、citemccode，删除只删没有被引用的项目。
# 货位、计量单位可新增、修改、删除（EAI）；自定义项档案、客户存货对照只能新增、删除（U8 不提供修改）。
# 两列主键的档案不收 template。
# 之后：计量单位组、结算方式、收发类别、采购类型、销售类型、地区分类、银行档案可新增、修改、删除（EAI）。
# 币种、凭证类别可新增（EAI，U8PzInsert 的组件；桥 ArcGl）、修改、删除（受控 SQL）；会计科目仍只读。
# 汇率、供应商联系人可新增（EAI 分发器；桥 ArcExchWrite、ArcVenContact）、修改、删除（受控 SQL）。
# 原因码可新增、修改、删除（EAI，桥 ArcReason），get、list 与九类档案一样按 EAI 标签返回。
ARCHIVE_WRITE_DESC = "\n\n" + "\n\n".join(
    (
        section("其他可写档案", _WRITE_SIMPLE),
        *(section(label, items) for label, items in _WRITE_DETAIL),
        PARTNER_WRITE_DESC,
        FA_WRITE_DESC,  # 固定资产卡片、设备台账
    )
)
WriteArchiveName = Literal[
    "customer",
    "vendor",
    "inventory",
    "department",
    "person",
    "warehouse",
    "customer_class",
    "vendor_class",
    "inventory_class",
    "bank",
    "project",
    "position",
    "unit",
    "user_define",
    "customer_inventory",
    "unit_group",
    "settle_style",
    "rd_style",
    "purchase_type",
    "sale_type",
    "district_class",
    "aa_bank",
    "currency",
    "voucher_sign",
    "customer_bank",
    "vendor_bank",
    "customer_contact",
    "exchange_rate",
    "vendor_contact",
    "reason",
    # 固定资产卡片（新增、撤销本期新增）、设备台账（只能新增）
    "fa_card",
    "equipment",
]
# 修改：自定义项档案、客户存货对照 U8 不提供修改（EAI 修改报错，桥 400），只能新增、读取、删除。
ARCHIVE_UPDATE_DESC = ARCHIVE_WRITE_DESC + "\n\n" + section(
    "不能修改",
    ("`user_define`、`customer_inventory`：U8 不提供修改，删除后重新新增", "`fa_card`、`equipment`"),
)
UpdateArchiveName = Literal[
    "customer",
    "vendor",
    "inventory",
    "department",
    "person",
    "warehouse",
    "customer_class",
    "vendor_class",
    "inventory_class",
    "bank",
    "project",
    "position",
    "unit",
    "unit_group",
    "settle_style",
    "rd_style",
    "purchase_type",
    "sale_type",
    "district_class",
    "aa_bank",
    "currency",
    "voucher_sign",
    "customer_bank",
    "vendor_bank",
    "customer_contact",
    "exchange_rate",
    "vendor_contact",
    "reason",
]
DeleteArchiveName = Literal[
    "customer",
    "vendor",
    "inventory",
    "department",
    "person",
    "warehouse",
    "customer_class",
    "vendor_class",
    "inventory_class",
    "bank",
    "position",
    "unit",
    "user_define",
    "customer_inventory",
    "project",
    "unit_group",
    "settle_style",
    "rd_style",
    "purchase_type",
    "sale_type",
    "district_class",
    "aa_bank",
    "currency",
    "voucher_sign",
    "customer_bank",
    "vendor_bank",
    "customer_contact",
    "exchange_rate",
    "vendor_contact",
    "reason",
    # 撤销本期新增的固定资产卡片（code 是卡片编号）
    "fa_card",
]
# 读取按表列名（get 的 fields 是表列名，不是 EAI 标签）；只读档案只能 get、list（新增、修改、删除 400）。
_RO_ITEMS = (
    "只读（不能新增、修改、删除）：account、trade_class、customer_address、operator、role",
    "`account` 科目：按登录日期的年份",
    "`unit` 计量单位、`unit_group` 计量单位组、`settle_style` 结算方式、`voucher_sign` 凭证类别",
    "`currency` 币种：编码是币种名称",
    "`bank` 本单位开户银行、`rd_style` 收发类别、`purchase_type` 采购类型、`sale_type` 销售类型",
    "`district_class` 地区分类、`trade_class` 行业分类、`aa_bank` 银行档案（所属银行）",
    "`project` 项目：编码 `<项目大类>:<项目编码>`",
    "`position` 货位：class_code 是仓库",
    "`customer_address` 客户收货地址：编码 `<客户编码>:<地址编码>`",
    "`user_define` 自定义项档案：编码 `<自定义项号>:<档案值>`",
    "`customer_inventory` 客户存货对照：编码 `<客户编码>:<存货编码>`",
    "`exchange_rate` 汇率：编码 `<币种>:<年度>:<期间>[:<日>]`",
    "`exchange_rate` 的 get 另收 `<币种>:<yyyy-mm-dd>`，取该日期单据会用的汇率",
)
ARCHIVE_RO_DESC = "\n\n" + "\n\n".join(
    (
        section("按表列名读取", (*_RO_ITEMS, *FA_RO, FA_RO_DESC, *UA_RO, *PARTNER_RO_DESC)),
        section("权限", UA_PERM),
    )
)
ReadArchiveName = Literal[
    "customer",
    "vendor",
    "inventory",
    "department",
    "person",
    "warehouse",
    "customer_class",
    "vendor_class",
    "inventory_class",
    "account",
    "unit",
    "unit_group",
    "settle_style",
    "voucher_sign",
    "currency",
    "bank",
    "project",
    "position",
    "rd_style",
    "purchase_type",
    "sale_type",
    "district_class",
    "trade_class",
    "aa_bank",
    "customer_address",
    "user_define",
    "customer_inventory",
    "exchange_rate",
    "fa_card",
    "operator",
    "role",
    "customer_bank",
    "vendor_bank",
    "customer_contact",
    "vendor_contact",
    "reason",
    # 设备台账（EQ_EQData）
    "equipment",
]
