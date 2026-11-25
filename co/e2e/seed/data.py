"""空账套造数用的全部编码和名称。只用中性的测试编码，不含站点数据。

审批流的审批人编码不在这里：它们从账套里已发布的审批流 XML 现取（steps_people.py）。
缺省编码按这样的编码方案取：部门一级 5 位，客户、供应商分类一级 3 位，存货分类 2-2，收发类别 1-2，
结算方式一级 1 位，地区分类、货位一级 2 位。账套的编码方案不同时在 e2e 配置的 seed 段改（config.py），
造数前的预检（preflight.py）按账套的编码方案核对，不合就停下。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SAFE_CODE = re.compile(r"^[^\x00-\x1f'\"\\;]{1,30}\Z")

# ---- 可由 e2e 配置 seed 段覆盖的值（config.py）；改了以后 configure() 重算下面的派生值和档案表 ----

SEED_DATE = "2024-01-01"
# 部门：费用、销售、采购、档案、审批，顺序固定。
DEPARTMENT_CODES = ("SD001", "SD002", "SD003", "SD004", "SD005")
CUSTOMER_CLASS = "C10"
VENDOR_CLASS = "S10"
# 存货分类：一级、它下面的二级（存货挂在二级上）。
INVENTORY_CLASS_CODES = ("01", "0101")
# 收发类别：out / in 是一级（收发标志必填），其余是它们下面的末级。
RD = {
    "out": "1",
    "in": "2",
    "sale_out": "101",
    "material_out": "102",
    "transfer_out": "111",
    "purchase_in": "201",
    "product_in": "202",
    "transfer_in": "203",
    "other_in": "205",
}
RD_PARENT = {
    "sale_out": "out",
    "material_out": "out",
    "transfer_out": "out",
    "purchase_in": "in",
    "product_in": "in",
    "transfer_in": "in",
    "other_in": "in",
}
SETTLE_STYLE = "1"
DISTRICT = "01"
POSITION = "01"
# 总账科目和现金流量项目（科目、项目已在账套里）。缺省是 U8 标准一级科目（管理费用、其他应付款），
# 账套里这两个科目有下级时用 seed.gl.expense_account / payable_account 配成末级科目。
# bank_account 是通用占位值，只在造数查不到账套的银行科目、测试数据文件里也没有时才写进测试数据
# （见 emit.gl_payload）；配了 seed.gl.bank_account 就固定用配置的（BANK_FIXED）。
GL_BASE = {
    "expense_account": "6602",
    "payable_account": "2241",
    "bank_account": "1002990101",
    "cash_flow_item": "07",
}
BANK_FIXED = False
# 回归的新增-修改-删除用例要用的编码：必须在账套里不存在。
CYCLE_CODES = {
    "customer": ("C199901", "C199902", "C199903"),
    "vendor": ("S199901", "S199902", "S199903"),
    "department": ("SD990", "SD991", "SD992"),
    "warehouse": ("99", "98", "97"),
    "customer_class": ("C90", "C91", "C92"),
    "vendor_class": ("S90", "S91", "S92"),
    "inventory_class": ("99", "98", "97"),
    "person": ("SD00491", "SD00492", "SD00493"),
    "inventory": ("91990001", "91990002", "91990003"),
}

_CONFIGURABLE = (
    "SEED_DATE", "DEPARTMENT_CODES", "CUSTOMER_CLASS", "VENDOR_CLASS", "INVENTORY_CLASS_CODES", "RD", "SETTLE_STYLE",
    "DISTRICT", "POSITION", "GL_BASE", "BANK_FIXED", "CYCLE_CODES",
)
_DEFAULTS = {name: globals()[name] for name in _CONFIGURABLE}

CURRENCY = "人民币"


@dataclass(frozen=True)
class Arc:
    kind: str
    code: str
    fields: dict = field(default_factory=dict)
    template: str = ""


# 档案 → (表, 编码列)。存在性检查按这张表查，编码按库的排序规则比较。
KIND_TABLE = {
    "unit_group": ("ComputationGroup", "cGroupCode"),
    "unit": ("ComputationUnit", "cComunitCode"),
    "rd_style": ("Rd_Style", "cRdCode"),
    "settle_style": ("SettleStyle", "cSSCode"),
    "purchase_type": ("PurchaseType", "cPTCode"),
    "sale_type": ("SaleType", "cSTCode"),
    "district_class": ("DistrictClass", "cDCCode"),
    "aa_bank": ("AA_Bank", "cBankCode"),
    "bank": ("Bank", "cBCode"),
    "currency": ("foreigncurrency", "cexch_name"),
    "reason": ("Reason", "cReasonCode"),
    "customer_class": ("CustomerClass", "cCCCode"),
    "vendor_class": ("VendorClass", "cVCCode"),
    "inventory_class": ("InventoryClass", "cInvCCode"),
    "department": ("Department", "cDepCode"),
    "warehouse": ("Warehouse", "cWhCode"),
    "position": ("Position", "cPosCode"),
    "person": ("hr_hi_person", "cPsn_Num"),
    "customer": ("Customer", "cCusCode"),
    "vendor": ("Vendor", "cVenCode"),
    "inventory": ("Inventory", "cInvCode"),
}

# ---- 部门、人员 ----

DEPT_EXPENSE, DEPT_SALES, DEPT_PURCHASE, DEPT_ARCHIVE, DEPT_WORKFLOW = DEPARTMENT_CODES

SPEC_OPERATOR = "SD00201"
SALES_PERSON = "SD00202"
INSPECTORS = ("SD00301", "SD00302")
PURCHASE_PERSON = "SD00303"
# e2e 操作员在 UserHrPersonContro 里对应的人员。故意不是任何审批流的审批人：wf-qm04-cycle 要先看到
# 提交后操作员不是当前审批人（409 not_current_approver），再把待办改派给这个人员后同意。
OPERATOR_PERSON = "SD00401"

PERSON_TYPE = "101"
EMPLOY_STATE = "10"
EMPLOYMENT_FORM = "001"

# ---- 仓库、存货 ----

WH_PLAIN = ("06", "07")
WH_BINNED = "01"
UNIT_GROUP = "01"
UNIT = "01"
INV_CLASS_TOP, INV_CLASS = INVENTORY_CLASS_CODES
INV_TEMPLATE = "91010001"
INV_TEMPLATE_NAME = "造数模板存货"
TAX_RATE = 13

# 存货变体：(编码, 名称, 复制后用 SQL 覆盖的标志)。模板本身是 bSale..bBomSub 全 1、bPropertyCheck=bInvBatch=0。
_ALL_OFF = {"bSelf": 0, "bComsume": 0, "bProducing": 0, "bBomMain": 0, "bBomSub": 0, "bPropertyCheck": 0, "bInvBatch": 0}
INVENTORY_ROLES = (
    # 母件打开质检：生产订单行才带 QcFlag=1，能参照做产品报检单（QM04 造数要用）。
    ("91020001", "造数BOM母件", {"bSelf": 1, "bBomMain": 1, "bPropertyCheck": 1}),
    ("91020002", "造数BOM子件1", {"bPurchase": 1, "bBomSub": 1, "bSelf": 0, "bBomMain": 0}),
    ("91020003", "造数BOM子件2", {"bPurchase": 1, "bBomSub": 1, "bSelf": 0, "bBomMain": 0}),
    ("91020004", "造数BOM子件3", {"bPurchase": 1, "bBomSub": 1, "bSelf": 0, "bBomMain": 0}),
    ("91020005", "造数来料检验件", {"bPurchase": 1, "bPropertyCheck": 1, "bInvBatch": 0}),
    ("91020006", "造数批次件", {"bInvBatch": 1}),
    ("91020007", "造数普通件", dict(_ALL_OFF, bSale=1, bPurchase=1)),
    # 第二个母件关闭质检：生产订单行 QcFlag=0，回归 product-in-from-mo（参照生产订单入库）挑的是这种行。
    ("91020008", "造数BOM免检母件", {"bSelf": 1, "bBomMain": 1, "bPropertyCheck": 0}),
)
PLAIN_INVENTORY = "91020007"
PLAIN_BOM_PARENT = "91020008"

# ATO 固定数据（步骤 ato，steps_docs_ato.py）：两个选项子件、一个选项类、一个 ATO 模型、两者共用的必选子件，
# 和一个普通 ATO 件（不是模型，标准物料清单，不用选配）。同样按模板复制，再用 SQL 改标志。选项类不能是自制 / 采购 /
# 委外（v_bas_inventory.InvAttr 里这三样优先，选项类就不再是 4）；ATO 模型要同时是模型（bInvModel：模型才能挂选项类、
# 子件才能选配），自制、可销售、允许生产订单。U8 实测：ATO 模型件的销售订单行没有选配不能审核，普通 ATO 件可以。
ATO_OPTION_A = "91030001"
ATO_OPTION_B = "91030002"
ATO_CLASS = "91030003"
ATO_MODEL = "91030004"
ATO_BASE = "91030005"
ATO_STD = "91030006"
_ATO_PART = {"bPurchase": 1, "bBomSub": 1, "bSelf": 0, "bBomMain": 0, "bSale": 0}
ATO_ROLES = (
    (ATO_OPTION_A, "造数ATO选项A", _ATO_PART),
    (ATO_OPTION_B, "造数ATO选项B", _ATO_PART),
    (
        ATO_CLASS,
        "造数ATO选项类",
        {"bCheckItem": 1, "bSelf": 0, "bPurchase": 0, "bProxyForeign": 0, "bSale": 0, "bComsume": 0,
         "bProducing": 0, "bBomMain": 1, "bBomSub": 1},
    ),
    (
        ATO_MODEL,
        "造数ATO模型",
        {"bATOModel": 1, "bInvModel": 1, "bSelf": 1, "bSale": 1, "bProductBill": 1, "bBomMain": 1, "bBomSub": 0,
         "bPurchase": 0, "bComsume": 0, "bCheckItem": 0, "bPropertyCheck": 0},
    ),
    (ATO_BASE, "造数ATO基础件", _ATO_PART),
    (
        ATO_STD,
        "造数ATO普通件",
        {"bATOModel": 1, "bInvModel": 0, "bSelf": 1, "bSale": 1, "bProductBill": 1, "bBomMain": 1, "bBomSub": 0,
         "bPurchase": 0, "bComsume": 0, "bCheckItem": 0, "bPropertyCheck": 0},
    ),
)
# 销售订单两行（存货, 数量）：第 1 行普通 ATO 件不选配，第 2 行 ATO 模型选配 ATO_OPTION_A。
ATO_SO_LINES = ((ATO_STD, 5), (ATO_MODEL, 3))

# ---- 总账、收发类别（由上面可覆盖的值派生，configure() 重算） ----


def _gl() -> dict:
    base = GL_BASE
    return {
        "expense_account": base["expense_account"],
        "expense_dept": DEPT_EXPENSE,
        "payable_account": base["payable_account"],
        "bank_account": base["bank_account"],
        "cash_flow_item": base["cash_flow_item"],
    }


GL = _gl()
SALE_OUT_RD = RD["sale_out"]
TRANSFER_OUT_RD = RD["transfer_out"]
TRANSFER_IN_RD = RD["transfer_in"]
# 材料出库、产成品入库生单时桥要求表头带收发类别（没有缺省值）；写进测试数据 stock.material_out_rd / product_in_rd。
MATERIAL_OUT_RD = RD["material_out"]
PRODUCT_IN_RD = RD["product_in"]
PURCHASE_IN_RD = RD["purchase_in"]
RD_OTHER_IN = RD["other_in"]
TRADE_CODE = "A"


def person(code: str, name: str, dept: str) -> Arc:
    # rIDType 故意不传，由桥补默认值 0。
    return Arc(
        "person",
        code,
        {
            "name": name,
            "rsex": 1,
            "rEmployState": EMPLOY_STATE,
            "rpersontype": PERSON_TYPE,
            "EmploymentForm": EMPLOYMENT_FORM,
            "bpsnperson": 1,
            "cdept_num": dept,
            "cdepcode": dept,
            "dpvaliddate": SEED_DATE,
        },
    )


def _dept(code: str, name: str) -> Arc:
    return Arc("department", code, {"name": name, "rank": 1, "endflag": 1, "ddepbegindate": SEED_DATE})


def _warehouse(code: str, name: str) -> Arc:
    return Arc("warehouse", code, {"name": name, "valuestyle": "全月平均法", "whmanage": 0, "bmrp": 0, "bincost": 1})


def _customer(number: int, tax: bool) -> Arc:
    code = "C" + str(number).rjust(4, "0")
    tag = str(number).rjust(2, "0")
    fields = {
        "name": "测试客户" + tag + "有限公司",
        "abbrname": "测试客户" + tag,
        "sort_code": CUSTOMER_CLASS,
        "seed_date": SEED_DATE,
        "super_dept": DEPT_SALES,
        "spec_operator": SPEC_OPERATOR,
        "cCusTradeCCode": TRADE_CODE,
        "ccusexch_name": CURRENCY,
    }
    if tax:
        fields["tax_reg_code"] = "91000000SEEDC" + code[1:].rjust(3, "0")[-3:]
    return Arc("customer", code, fields)


def _vendor(number: int) -> Arc:
    code = "S" + str(number).rjust(4, "0")
    tag = str(number).rjust(2, "0")
    fields = {
        "name": "测试供应商" + tag + "有限公司",
        "abbrname": "测试供应商" + tag,
        "sort_code": VENDOR_CLASS,
        "seed_date": SEED_DATE,
        "tax_in_price_flag": 1,
        "cvenexch_name": CURRENCY,
        "tax_reg_code": "91000000SEEDS" + code[1:].rjust(3, "0")[-3:],
    }
    return Arc("vendor", code, fields)


def _klass(kind: str, code: str, name: str, rank: int, end: int) -> Arc:
    return Arc(kind, code, {"name": name, "rank": rank, "end_rank_flag": end})


UNITS = (
    Arc("unit_group", UNIT_GROUP, {"name": "无换算", "type": 0}),
    Arc("unit", UNIT, {"name": "个", "group_code": UNIT_GROUP}),
)

def _rd_styles() -> tuple[Arc, ...]:
    # 先一级（收发标志必填），再末级。
    names = (
        ("out", "出库", 0),
        ("in", "入库", 1),
        ("sale_out", "销售出库", 0),
        ("material_out", "材料出库", 0),
        ("transfer_out", "调拨出库", 0),
        ("purchase_in", "采购入库", 1),
        ("product_in", "产成品入库", 1),
        ("transfer_in", "调拨入库", 1),
        ("other_in", "其他入库", 1),
    )
    return tuple(Arc("rd_style", RD[role], {"name": name, "rsflag": flag}) for role, name, flag in names)


def _biz_types() -> tuple[Arc, ...]:
    return (
        Arc("purchase_type", "CG", {"name": "普通采购", "rstype_code": PURCHASE_IN_RD}),
        Arc("sale_type", "XS", {"name": "普通销售", "rstype_code": SALE_OUT_RD}),
    )


def _classes() -> tuple[Arc, ...]:
    return (
        _klass("customer_class", CUSTOMER_CLASS, "测试客户类", 1, 1),
        _klass("vendor_class", VENDOR_CLASS, "测试供应商类", 1, 1),
        _klass("inventory_class", INV_CLASS_TOP, "测试存货大类", 1, 0),
        _klass("inventory_class", INV_CLASS, "测试存货类", 2, 1),
    )


def _departments() -> tuple[Arc, ...]:
    names = ("测试费用部", "测试销售部", "测试采购部", "测试档案部", "测试审批部")
    return tuple(_dept(code, name) for code, name in zip(DEPARTMENT_CODES, names))


def _persons() -> tuple[Arc, ...]:
    return (
        person(SPEC_OPERATOR, "测试专管业务员", DEPT_SALES),
        person(SALES_PERSON, "测试销售员", DEPT_SALES),
        person(INSPECTORS[0], "测试检验员1", DEPT_PURCHASE),
        person(INSPECTORS[1], "测试检验员2", DEPT_PURCHASE),
        person(PURCHASE_PERSON, "测试采购员", DEPT_PURCHASE),
        person(OPERATOR_PERSON, "测试操作员", DEPT_ARCHIVE),
    )


RD_STYLES = _rd_styles()

SETTLE = (Arc("settle_style", SETTLE_STYLE, {"name": "现金"}),)

BIZ_TYPES = _biz_types()

DISTRICTS = (Arc("district_class", DISTRICT, {"name": "测试地区"}),)

BANKS = (
    Arc("aa_bank", "00001", {"name": "测试银行"}),
    Arc(
        "bank",
        "001",
        {"name": "测试人民币户", "account": "1000000001", "cbankcode": "00001", "ccurrencyname": CURRENCY},
    ),
)

CURRENCIES = (Arc("currency", "美元", {"code": "USD", "caltype": 1, "precision": 4}),)

# 不良品处理单（QM05 / QM06）表体的不良原因：原因码档案，所属类型 1（不良品原因）。用例按 iReasontype=1、01 优先挑。
QM_REASON = "01"
REASONS = (Arc("reason", QM_REASON, {"name": "检测指标不合格", "Reasontype": 1}),)

CLASSES = _classes()

DEPARTMENTS = _departments()

WAREHOUSES = (
    _warehouse(WH_PLAIN[0], "测试平仓" + WH_PLAIN[0]),
    _warehouse(WH_PLAIN[1], "测试平仓" + WH_PLAIN[1]),
    _warehouse(WH_BINNED, "测试货位仓" + WH_BINNED),
)


def _positions() -> tuple[Arc, ...]:
    return (Arc("position", POSITION, {"name": "测试货位" + POSITION, "warehouse_code": WH_BINNED}),)


POSITIONS = _positions()

PERSONS = _persons()


# C0001 带税号（开票来源要用）；C9999、S9999 留着不用（没有往来的客户、供应商）。
def _customers() -> tuple[Arc, ...]:
    return tuple(_customer(n, n == 1) for n in range(1, 7)) + (_customer(9999, False),)


def _vendors() -> tuple[Arc, ...]:
    return (_vendor(1), _vendor(9999))


CUSTOMERS = _customers()
VENDORS = _vendors()

INVENTORIES = tuple(
    Arc("inventory", code, {"name": name, "specs": "seed"}, INV_TEMPLATE) for code, name, _flags in INVENTORY_ROLES
)
ATO_INVENTORIES = tuple(
    Arc("inventory", code, {"name": name, "specs": "seed"}, INV_TEMPLATE) for code, name, _flags in ATO_ROLES
)


def defaults() -> dict:
    """可覆盖值的缺省（深拷贝），config.apply 在它上面合并配置。"""
    return {name: (dict(value) if isinstance(value, dict) else value) for name, value in _DEFAULTS.items()}


def configure(values: dict) -> None:
    """换掉可覆盖的值，再重算派生的编码和档案表。values 的键只能是 _CONFIGURABLE 里的名字。

    造数步骤模块导入时就取了这里的值，所以只能在导入 runner / steps_* 之前调用（见 config.apply）。
    """
    unknown = sorted(set(values) - set(_CONFIGURABLE))
    if unknown:
        raise ValueError("不能覆盖的造数值：" + "、".join(unknown))
    names = globals()
    names.update(values)
    _derive(names)


def _derive(names: dict) -> None:
    rd = names["RD"]
    names.update(
        DEPT_EXPENSE=DEPARTMENT_CODES[0], DEPT_SALES=DEPARTMENT_CODES[1], DEPT_PURCHASE=DEPARTMENT_CODES[2],
        DEPT_ARCHIVE=DEPARTMENT_CODES[3], DEPT_WORKFLOW=DEPARTMENT_CODES[4],
        INV_CLASS_TOP=INVENTORY_CLASS_CODES[0], INV_CLASS=INVENTORY_CLASS_CODES[1],
        SALE_OUT_RD=rd["sale_out"], TRANSFER_OUT_RD=rd["transfer_out"], TRANSFER_IN_RD=rd["transfer_in"],
        MATERIAL_OUT_RD=rd["material_out"], PRODUCT_IN_RD=rd["product_in"], PURCHASE_IN_RD=rd["purchase_in"],
        RD_OTHER_IN=rd["other_in"],
    )
    names.update(GL=_gl(), RD_STYLES=_rd_styles(), BIZ_TYPES=_biz_types(), CLASSES=_classes())
    names.update(DEPARTMENTS=_departments(), PERSONS=_persons(), CUSTOMERS=_customers(), VENDORS=_vendors())
    names.update(
        SETTLE=(Arc("settle_style", SETTLE_STYLE, {"name": "现金"}),),
        DISTRICTS=(Arc("district_class", DISTRICT, {"name": "测试地区"}),),
        POSITIONS=_positions(),
    )


def bank_account() -> str:
    """银行科目：测试数据配了 gl.bank_account 用它，否则用 GL 里的值（seed.gl.bank_account 或通用占位值）。"""
    from co.e2e.testdata import DataMissing, value

    try:
        return value("gl.bank_account")
    except DataMissing:
        return GL["bank_account"]
