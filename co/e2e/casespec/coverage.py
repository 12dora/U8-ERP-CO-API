"""按改动挑用例：git 改动的文件路径 → 需要跑的用例名。纯函数，不调 git、不连服务器。

规则表 PATH_RULES 按顺序匹配（fnmatch，路径从仓库根算、用 /），第一条命中的规则说了算。
目标是用例名前缀：用例名等于它或以它开头就选中（精确名字也按前缀写）。
ALL 表示全部用例；NONE 表示只跑 ALWAYS（health、login）。

失败即全跑（fail closed）：桥源码、客户端、API、回归框架、SafePath.ps1 和桥的构建文件，
没有更具体的规则时一律全跑，表里漏掉的新文件只会多跑，不会漏跑。
文档、事件服务、单元测试之类只跑 ALWAYS；表外的其他路径同样只跑 ALWAYS。

e2e 直接调桥，不经过 api/u8co_api；API 文件按同名功能对到同一批用例，只为对照。
这只是开发时的过滤器：发版前必须跑全部用例（co/e2e/README.md 的「按改动挑用例」一节）。
"""

from __future__ import annotations

from fnmatch import fnmatchcase

ALWAYS = ("health", "login")
ALL = ("*",)
NONE: tuple[str, ...] = ()

# ---- 用例族（用例名前缀）。新增用例时确认它落在某一族里，否则 test_coverage 会报「不可达」。 ----
SALE = (
    "sale-",
    "dispatch-",
    "invoice-generate",
    "missing-404",
    "bad-action",
    "repeat-unverify",
    "downstream-unverify",
    "update-verified",
    "load-sale",
    "load-dispatch",
    "list-sale-",
)
PU = ("po-", "arrival-", "purchase-", "load-purchase", "load-arrival", "list-purchase-", "qm01-")  # qm01 参照到货单
STOCK = (
    "stock-",
    "other-",
    "close-other-in",
    "transfer-",
    "purchase-in-",
    "sale-out-",
    "product-in-",
    "material-out-",
    "binned-",
    "create-protected",
    "load-purchase_in",
    "load-other_",
    "load-product_in",
    "load-material_out",
    "load-sale_out",
    "load-transfer",
    "list-changed-since",
    "report-stock-",
    "report-position-stock",
    "report-batch-stock",
    "bridge-concurrency",  # 并发读单并新建、删除其他入库单
    "shape-change-",  # 形态转换单（调拨申请单 transfer-、盘点单 stock- 已在上面）
)
ARAP = ("ar-", "ap-", "arap-", "load-ar-", "load-ap-", "list-ar-", "list-ap-", "report-arap-")  # 含 list-ap-refund
ARAP += ("refund-voucher-",)  # 供应商退款制单（客户退款 ar-refund-voucher- 已在 ar- 里）
GL = ("gl-", "idempotency-gl-", "report-gl-", "report-close-status")
ARC = ("archive-", "customer-invoice-company-")
MO = ("mo-", "production-order-", "load-production-order", "qm02-")  # qm02 参照生产订单
MFG = ("material-out-", "product-in-", "load-material_out", "load-product_in")
BOM = ("bom-", "report-bom")
QM = ("qm0", "load-qm", "wf-qm", "purchase-in-from-qm", "purchase-in-qm-", "product-in-from-reject")
QM += ("qm-incoming-reject-", "qm-product-reject-", "qm-reject-")  # 不良品处理单生单、审核、删除
QM += ("qm-other-",)  # 其他报检单、其他检验单（读取 load-qm_other_* 已在 load-qm 里）
QM += ("qm11-", "qm15-")  # 其他报检单、其他检验单修改
WF = ("wf-", "opinion-too-long")
REPORT = ("report-",)
LIST = ("list-", "stock-current-filter")
META = ("meta-fields",)
IDEM = ("idempotency-",)
AUDIT = ("account-foreign",)
# 销售订单 / 采购订单锁定；锁定闸门挂在销售、采购的修改、删除、审核上。
LOCK = ("sale-order-lock", "po-lock", "sale-lock-bad-type")
EDIT = SALE + PU + STOCK + ARAP
# 写预演（含预演的拒绝）、名称解析、结构化错误。幂等结果查询 idempotency-get 在 IDEM 里。
DRY = ("dry-",)
RESOLVE = ("resolve-",)
ERRORS = ("errors-",)
# 单据搜索（复用列表的 SQL）、批量读取。字段标签 meta-fields-* 在 META 里，全部写路由的幂等键 idempotency-* 在 IDEM 里。
SEARCH = ("vouchers-search",)
BATCH = ("batch-load-", "archive-get-many")
# 期初记账（openings/post）。
OPENINGS = ("openings-",)
# 月末结账（periods/close）。
PERIODS = ("periods-",)
# 存货核算：正常单据记账 / 恢复记账（ia/post）、期末处理 / 取消（ia/period_end），回归只跑预演。
IA = ("ia-",)
# 应收票据：登记、结算、贴现、背书、分包结算（notes/*、arap/process 的票据分支）；另含退回、应付票据（ap-note-）。
NOTES = ("note-", "ap-note-")
# 应收坏账：发生、收回、计提（arap/bad_debt、arap/process 的坏账分支）。
BAD_DEBT = ("bad-debt-",)
# 写入策略（writePolicyFile）：临时换本通道桥的策略文件的一组用例。
WP = ("wp-",)
# 权限核对：perm/evaluate、perm/snapshot 与账套库独立算出的数据权限一致（Perm*.cs 已全跑；操作员档案是它的枚举来源）；
# 字段权限遮蔽 col-auth-（col_auth）。
PERM = ("perm-", "col-auth-")
# 固定资产卡片新增 / 撤销、变动单（读取 archive-read-fa-card、报表 report-fa-* 一并跑），设备台账读取、新增。
FA = ("fa-", "archive-read-fa-", "report-fa-")
EQ = ("equipment-", "archive-read-equipment")
# 历史发货单表头修正（vouchers/head_fix）：发货单、销售出库单、销售发票、往来明细的描述列。
HEAD_FIX = ("voucher-head-fix",)
# 销售订单表头自定义项写入（vouchers/so_defines）：订单表头 cdefineN，订单行与下游发货单、出库单、发票的整行快照。
SO_DEFINES = ("voucher-so-defines",)

_SRC = "co/bridge/src/"

# 桥的共享基础设施：分派、HTTP、STA 线程池、事务、DOM 行、登录与权限、配置、读单。改了就全跑。
_INFRA = (
    "AdoCreds.cs",
    "AdoXml.cs",
    "ApiResult.cs",
    "AppHost.cs",
    "Auth*.cs",
    "BillNo.cs",
    "CoRows.cs",
    "CoTrans.cs",
    "ComUtil.cs",
    "Config*.cs",
    "Crypto.cs",
    "Dispatch.cs",
    "DispatchRoutes.cs",  # 写闸门自检用的路由表访问器
    "DispatchMore.cs",  # Dispatch.Handle 的后段分派：每个请求都先经它（库存期初、退货申请单），返回 null 再走路由表
    "DispatchFixes.cs",  # Dispatch.Handle 路由表的受控 SQL 单据表头修正段（head_fix、so_defines 的登记）
    "DispatchLedger.cs",  # Dispatch 路由表的账务处理段（坏账、期初、月末结账、存货核算、自动转账的登记）
    "DocLocks.cs",
    "DomRows*.cs",
    # 预演：每个写任务都经 DryRunRun.Run、DryRunReq（ParseNew）、DryRun.End，不只是预演时。其余 DryRun* 只在预演时起作用。
    "DryRun.cs",
    "DryRunReq.cs",
    "DryRunRun.cs",
    "FileAcl.cs",
    "HttpServer.cs",
    "IdemReq.cs",  # 全部写路由都经 IdemReq.Take（ParseNew），不只是带幂等键时
    "LicenseRetry.cs",  # U8Session.Open 里的登录重试，每次登录都走
    "LicenseState.cs",
    "LoginCache*.cs",  # 登录复用：每个要登录的请求都经 LoginCache.Acquire / Release
    "MetaAccess.cs",  # 其实是 StockDom、PuArr、Requests、SaleGen、StockGen 的 partial class
    "Json.cs",
    "NestedFields.cs",  # Json.CheckMap 的数组字段登记（检验单表头 items、物料清单行替代料 / 定位符），每个写请求都经过
    "Kinds.cs",
    "Paths.cs",
    "Perm*.cs",
    "Program.cs",
    "Requests*.cs",
    "RouteClass.cs",
    "WedgedChainSelfTest.cs",  # 「登录状态不是 0」自愈的失败计数链自检，只在 --selftest 跑
    "WedgedLoginSelfTest.cs",  # 同上，登录换线与探测的自检，只在 --selftest 跑
    "Wedged*.cs",  # 「登录状态不是 0」自愈：登录失败计数、换写线、请求重启；每个要登录的请求都经 WedgedLogin
    "ReadLeakSelfTest.cs",  # 读权限收紧的自检（GL、FA、经营管理、幂等查询），只在 --selftest 跑
    "TestAccountRouteSelfTest.cs",  # 第二级写入总开关的路由矩阵自检，只在 --selftest 跑
    "ReadOnlyGate*.cs",  # 只读账套：所有写路由登录前、出队后都经过它
    "ReadOnlyException*.cs",  # 只读例外的用例(从 ReadOnlyGateSelfTest 拆出)
    "ReadOnlyVoucherOps*.cs",  # 只读例外 type=vouchers 的 (op, voucher) 登记表：只读账套上的单据写都经过它
    "AsciiIgnoreCase.cs",  # 只读例外的操作员比较规则（与 API 一致）
    "Rows*.cs",
    "SqlRead.cs",
    "Sta*.cs",
    "OperatorPin*.cs",  # 写线程按操作员固定，每个要登录的任务都经它
    "Tpl*.cs",
    "U8*.cs",
    "VoucherAttach.cs",  # 单据附件列表，还没有用例
    "VoucherRead.cs",
    "Watchdog.cs",  # 每个请求登记忙闲，健康检查按它回 503
    "StopGuard.cs",  # 服务停止限时退出（OnStop、看门狗强制结束前报告已停止）
    "WorkContext.cs",
    "WorkItem.cs",
    "WorkRun.cs",
    "WriteGate*.cs",  # 全局写闸门：所有写路由都经过它
    "WriteClass*.cs",  # 写入分类：所有写路由登录前、出队后都经过它
    "WriteQuota*.cs",  # 账套写入限额：策略开着时每个写任务都占、结算名额
)

# 桥源码按文件名前缀分族。放在 _INFRA 之后：Sta*、Perm* 等已在上面全跑。
_BRIDGE = (
    # 写预演：模式表、预演映像（DryRunModes / Preview / Rows / Value …）只在预演时起作用，跑预演用例；
    # 每次写都经过的 DryRun.cs / DryRunReq.cs / DryRunRun.cs 在 _INFRA 里全跑。各业务的预演文件另跑该业务。放在业务前缀之前。
    ("DryRun*.cs", DRY),
    ("MetaDryRun*.cs", DRY + META),
    ("GlDry*.cs", DRY + GL),
    ("EaiDistribute*.cs", ARC + STOCK),  # EAI 分发器（库存期初导入、汇率与供应商联系人新增）
    ("ArcDry*.cs", DRY + ARC),
    ("Arap*Dry*.cs", DRY + ARAP),
    ("BomDry*.cs", DRY + BOM),
    ("MoDry*.cs", DRY + MO),
    ("StockCallDry*.cs", DRY + STOCK),
    ("*Dry*.cs", DRY + EDIT),
    ("DocMark*.cs", DRY + SALE + PU + ARAP),  # 预演登记新单据和上游单据（销售、采购、应收应付）
    ("ArcUa*.cs", ARC + PERM),  # 操作员、角色档案；权限核对靠它枚举操作员（在 Arc*.cs 之前）
    ("ArcFa*.cs", ARC + FA),  # 固定资产卡片读取、新增、撤销（在 Arc*.cs 之前）
    ("ArcEq*.cs", ARC + EQ),  # 设备台账（在 Arc*.cs 之前）
    ("ReportsFamilies*.cs", REPORT),  # rpt 按岗位的只读报表族登记与自检
    ("ReportsQmSelfTest.cs", REPORT),  # rpt 质量报表自检（在 Qm*.cs 之前）
    ("MfgSelfTest.cs", REPORT),  # rpt 生产报表自检（在 Mfg*.cs 之前）
    ("PuSelfTest.cs", REPORT),  # rpt 采购报表自检（在 Pu*.cs 之前）
    ("StockAgeSelfTest.cs", REPORT),  # rpt 库龄报表自检（在 Stock*.cs 之前）
    ("SaSelfTest.cs", REPORT),  # rpt 销售报表自检
    ("FinSelfTest.cs", REPORT),  # rpt 收付款、票据、现金流量报表自检
    ("AcctSelfTest.cs", REPORT),  # rpt 结账检查、存货核算、成本报表自检
    ("CaInputsSelfTest.cs", REPORT),  # 成本核算期间数据报表 ca_inputs 自检（在 Ca*.cs 之前）
    ("PeriodCloseSql.cs", REPORT + ("periods-",)),  # rpt 月结检查与结账检查报表共用的 SQL
    ("FaWriteSelfTest.cs", FA + EQ),  # 固定资产写入与设备台账的自检
    ("Fa*.cs", FA),  # 固定资产卡片、变动单的请求校验、EAI 报文、期间闸门
    ("ArcResolve*.cs", RESOLVE),  # 名称解析（在 Arc*.cs 之前）
    ("IdemGet*.cs", IDEM),  # 幂等结果查询 idempotency-get
    ("MetaFields*.cs", META),  # 可写字段探测、字段标签 meta/fields（在 Meta*.cs 之前，单独标出）
    ("VoucherSearch*.cs", SEARCH),  # 单据搜索（列表的 ListKind / ListSql 改了也跑它，见 List*.cs）
    ("TaxInvoiceNo*.cs", SEARCH),  # 数电号匹配与核销字段（按账套配置的发票号列）
    ("DescPaging*.cs", SEARCH + ("report-order-execution",)),  # 单据搜索、订单执行的 desc 键集翻页
    ("LoadMany*.cs", BATCH),  # 批量读单
    ("VoucherScope*.cs", LIST + BATCH),  # 同表分两个类型的单据（发货单 / 退货单，到货单 / 采购退货单）的读取核对：list-* 用例按类型条件，批量读单同走 LoadOne
    ("ArcGetMany*.cs", BATCH),  # 批量读档案（在 Arc*.cs 之前）
    ("VoucherLock*.cs", LOCK + SALE + PU),
    ("VoucherHeadFix*.cs", HEAD_FIX),  # 发货单表头修正
    ("VoucherSoDefines*.cs", SO_DEFINES),  # 销售订单表头自定义项写入
    # 无来源发货单、先开票发票、到货单、材料出库单；名单也给 meta。无来源销售出库单也从 SrcLess.TryCreate 进。
    ("SrcLess*.cs", SALE + PU + MFG + META + ("dry-sale-out-nosrc",)),
    ("StockGenSale*.cs", STOCK + SALE),  # 销售出库参照发货单（含批量）
    ("StockSaleOut*.cs", STOCK + META + ("dry-sale-out-nosrc",)),  # 无来源销售出库单新增（名单也给 meta）
    ("StockGenRed*.cs", STOCK + PU),  # 红字采购入库参照采购退货单
    ("StockGenArr*.cs", STOCK + PU),  # 蓝字采购入库参照到货单
    ("StockGenRej*.cs", STOCK + PU + QM),  # 蓝字采购入库参照来料不良品处理单
    ("RejSrc*.cs", QM + MFG),  # 不良品处理单入库的来源规则（采购入库、产成品入库共用）
    ("StockPurInRed*.cs", STOCK + PU),
    ("MfgGenMo*.cs", MFG + MO),  # 产成品入库参照生产订单
    ("ArapAudit*.cs", ARAP + SALE + PU),  # 采购发票应付审核、销售发票应收审核（读取的 state 也经过它）
    ("SaleGenApply*.cs", SALE + ("sale-return-apply-",)),  # 退货申请单新增、退货单参照退货申请单（在 Sale*.cs 之前）
    ("Sale*.cs", SALE),
    ("SaSession.cs", SALE),
    ("So*.cs", SALE),
    ("AccDefaults*.cs", PU + ARC + ARAP),  # 账套缺省（本位币、默认采购类型）：采购单据、客商档案、核销都读
    ("OpeningsArap*.cs", OPENINGS + ARAP),  # 应收 / 应付期初单据（在 Opening*.cs 之前）
    ("OpeningIa*.cs", ("openings-ia-",) + IA),  # 存货核算期初记账（在 Opening*.cs 之前）
    ("Opening*.cs", OPENINGS + PU),  # 期初记账（采购期初记账后采购发票才能保存）
    # 内嵌脚本的执行：存货核算路由、存货的月末结账（close.sql / unclose.sql）和存货核算期初记账（qc_*.sql）都经它
    ("SqlScript*.cs", IA + PERIODS + ("openings-ia-",)),
    ("Ia*.cs", IA),  # 存货核算路由
    ("TestAccountGate*.cs", OPENINGS + IA + PERIODS),  # 测试账套名单：期初记账、期初单据、存货核算、月末结账都经它
    ("Period*.cs", PERIODS + GL),  # 月末结账（总账记账只能记第一个未结账期间）
    ("PositionAdjust*.cs", ("position-adjust-",)),  # 货位调整单（ST 19）
    ("StockDomAdjust*.cs", STOCK + ("position-adjust-",)),  # 货位调整单的表体 DOM，在 Stock*.cs 之前
    ("InvPriceAdjust*.cs", ("list-inv-price-adjust",)),  # 存货调价单（只读）
    ("ReturnsApply*.cs", ("list-sale-return-apply", "load-sale-return-apply", "sale-return-apply-")),  # 退货申请单（可写）
    ("Pu*.cs", PU),  # 含 Purchase*、PuApp*（请购单）、PuSession、PuInvRed（红字采购发票）
    ("Stock*.cs", STOCK),
    ("List*.cs", LIST + SEARCH),  # 单据搜索复用列表的类型表和 SQL
    ("ReportsArapWriteoff*.cs", REPORT + ARAP),  # 核销记录查询，ar-writeoff 用例里核对
    ("Reports*.cs", REPORT),
    ("ArapProcVoucherNotes*.cs", NOTES + ARAP),  # 票据处理制单（PJ 凭证），在 Arap*.cs 之前
    ("ArapBad*.cs", BAD_DEBT + ARAP),  # 坏账发生、收回、计提，在 Arap*.cs 之前
    ("ArapProc*Bad*.cs", BAD_DEBT + ARAP),  # 坏账的取消、制单、处理列表分支
    ("Arap*.cs", ARAP),
    ("Arc*.cs", ARC),
    ("Gl*.cs", GL),
    ("Mo*.cs", MO),
    ("Mfg*.cs", MFG),
    ("Bom*.cs", BOM),
    ("SubstituteRows.cs", MO + BOM),  # 生产订单子件、物料清单子件替代料共用的对外形状
    ("SubstituteReq.cs", MO + BOM),  # 替代料请求与存货规则（物料清单行、生产订单子件共用）
    ("NotesProcReturn*.cs", ("note-return-", "ap-note-")),  # 票据退回 9C（应收 R0、应付 P0），在 Notes*.cs 之前
    ("NotesAp*.cs", ("ap-note-",)),  # 应付票据（仅测试账套）
    ("Notes*.cs", NOTES),  # 票据登记、读取、处理、取消处理
    ("Qm*.cs", QM),
    ("Wf*.cs", WF),
    ("Workflow*.cs", WF),
    ("TaskOrphans*.cs", WF),  # 审批流操作之后清孤儿任务
    ("Edit*.cs", EDIT),  # 修改分派：销售、采购、库存、应收应付
    ("Idem*.cs", IDEM),
    ("Meta*.cs", META),
    ("License*.cs", ("health",) + META),
    ("Sig*.cs", ("health",) + META),  # COM 签名自检，结果在 meta 的 features 里
    ("WritePolicy*.cs", ("health",) + WP),  # 写入策略：模型、解析、重载、判定，健康检查带 write_policy
    ("Audit*.cs", AUDIT),  # 每个请求都写审计；login 已覆盖写入，这两个用例核对名单外账套的审计行
    ("SelfTest.cs", ("bad-signature",)),  # 启动自检（签名测试向量、配置校验）
    ("MgmtGlSelfTest.cs", ("report-mgmt-pnl", "report-mgmt-meta", "report-mgmt-cash-stock")),  # 经营管理报表（总账口径）自检
    # vouchers/create 的其余类型：其他报检单无来源新增、采购手工结算、固定资产变动单。
    ("DispatchCreate.cs", ("qm-other-", "purchase-settle-manual-", "purchase-settle-rd-offset", "purchase-settle-inv-offset") + FA),
)

# 嵌进桥的 SQL 脚本（co/bridge/sql/**）。月末结账、取消结账的脚本也给 periods/close 的存货模块用。
_SQL = (
    ("co/bridge/sql/ia/close.sql", IA + PERIODS),
    ("co/bridge/sql/ia/unclose.sql", IA + PERIODS),
    ("co/bridge/sql/ia/qc_*.sql", ("openings-ia-",)),  # 存货核算期初记账 / 取消（openings/post module=ia）
    ("co/bridge/sql/arap/bad_*.sql", BAD_DEBT + ARAP),  # 坏账脚本
    ("co/bridge/sql/arap/*", ARAP),  # 汇兑损益等应收应付处理脚本
    ("co/bridge/sql/*", IA),
)

_CLIENT = (
    ("co/client/tests/*", NONE),
    ("co/client/u8co_cli*.py", NONE),  # 命令行，e2e 不用
    ("co/client/u8co_settings.py", NONE),  # 只给命令行用
    ("co/client/u8co_reports*.py", REPORT),
    ("co/client/u8co_meta.py", META),
    ("co/client/u8co_gl_arc.py", GL + ARC + LIST + REPORT),  # 报表复用它的参数校验
    ("co/client/u8co_partner.py", ARC),  # 银行账户、联系人的档案名
    ("co/client/u8co_assist.py", DRY + RESOLVE + IDEM),  # client.dry()、resolve、idempotency_get（e2e 直接拼正文，不经它们）
    ("co/client/u8co_lookup.py", META + SEARCH + BATCH),  # meta_fields、search、load_many、get_many（e2e 同样直接拼正文）
    ("co/client/u8co_openings.py", OPENINGS),  # 期初记账
    ("co/client/u8co_periods.py", PERIODS),  # 月末结账
    ("co/client/u8co_ia.py", IA + PERIODS),  # 存货核算；长时操作的读超时也管 periods/close
    ("co/client/u8co_so_defines.py", SO_DEFINES),  # 销售订单表头自定义项写入（e2e 直接拼正文）
    ("co/client/u8co_idem.py", DRY + IDEM),  # 写路由表 WRITE_ROUTES：client.call 的预演、client.keyed 都按它
)

_API = (
    ("api/u8co_api/co_models_reports_writeoff.py", REPORT + ARAP),
    ("api/u8co_api/co_models_reports*.py", REPORT),
    ("api/u8co_api/co_routes_reports.py", REPORT),
    ("api/u8co_api/co_models_arc*.py", ARC),
    ("api/u8co_api/co_models_head_fix.py", HEAD_FIX),  # 发货单表头修正
    ("api/u8co_api/co_models_so_defines.py", SO_DEFINES),  # 销售订单表头自定义项写入
    ("api/u8co_api/co_models_bom.py", BOM),
    ("api/u8co_api/co_models_mo.py", MO),
    ("api/u8co_api/co_models_wf.py", WF),
    ("api/u8co_api/co_meta.py", META),
    ("api/u8co_api/co_models_list.py", LIST),
    ("api/u8co_api/co_models_gl.py", GL),
    ("api/u8co_api/co_models_gl_post.py", GL),  # 记账
    ("api/u8co_api/co_models_gl_unpost.py", GL),  # 取消记账
    ("api/u8co_api/co_models_gl_reverse.py", GL),  # 红字冲销
    ("api/u8co_api/co_models_gl_transfer.py", GL),  # 期间损益结转、自定义转账
    ("api/u8co_api/co_routes_gl_transfer.py", GL),
    ("api/u8co_api/co_routes_gl_arc.py", GL + ARC + LIST),
    ("api/u8co_api/co_gen_sale_return.py", ("sale-return-",)),
    ("api/u8co_api/co_gen_source.py", PU + QM),
    ("api/u8co_api/co_models_arap.py", ARAP),
    ("api/u8co_api/co_models_writeoff_auto.py", ARAP),  # 自动核销
    ("api/u8co_api/co_models_arap_voucher.py", ARAP),  # 应收 / 应付制单
    ("api/u8co_api/co_routes_arap_voucher.py", ARAP),
    ("api/u8co_api/errors.py", ERRORS + DRY),  # 结构化错误（field / hint / retryable）
    ("api/u8co_api/co_models_lookup.py", META + SEARCH + BATCH),  # 字段标签、单据搜索、批量读取
    ("api/u8co_api/co_routes_lookup.py", META + SEARCH + BATCH),
    ("api/u8co_api/co_models_openings.py", OPENINGS),  # 期初记账
    ("api/u8co_api/co_routes_openings.py", OPENINGS),
    ("api/u8co_api/co_models_periods.py", PERIODS),  # 月末结账
    ("api/u8co_api/co_routes_periods.py", PERIODS + IA),  # 存货核算路由也在这里
    ("api/u8co_api/co_models_ia.py", IA),  # 存货核算
    ("api/u8co_api/co_models_stock_opening.py", STOCK),  # 期初结存单：不能修改、新增响应 docs
    ("api/u8co_api/co_models_notes_*.py", NOTES),  # 票据登记、读取、处理
    ("api/u8co_api/co_routes_notes_*.py", NOTES),
    ("api/u8co_api/co_models_arap_bad.py", BAD_DEBT + ARAP),  # 坏账
)

_DOCS = (
    ("*.md", NONE),
    ("docs/*", NONE),
    ("README*", NONE),
    ("CHANGELOG*", NONE),
    ("LICENSE", NONE),
    ("NOTICE", NONE),
    ("events/*", NONE),
    ("scripts/docs/*", NONE),
    ("api/tests/*", NONE),
    ("co/e2e/tests/*", NONE),
    ("co/e2e/casespec/coverage.py", NONE),  # 只影响挑选本身，由单元测试守
)

# 兜底：这些目录里表中没有的文件一律全跑。必须放在表尾。
FAIL_CLOSED = (
    "co/bridge/*",  # 源码和构建、安装文件（build.ps1、install.ps1、*.config、config.example.json）
    "co/client/*",
    "api/u8co_api/*",
    "co/e2e/*",  # 回归框架本身，含 casespec
    "co/SafePath.ps1",
    "co/__init__.py",
)

PATH_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    _DOCS
    + tuple((_SRC + name, ALL) for name in _INFRA)
    + tuple((_SRC + glob, targets) for glob, targets in _BRIDGE)
    + _SQL
    + _CLIENT
    + _API
    + tuple((glob, ALL) for glob in FAIL_CLOSED)
)


def normalize_path(path: str) -> str:
    text = (path or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def rule_for(path: str) -> tuple[str, tuple[str, ...]] | None:
    """第一条命中的规则 (glob, 目标)；表外路径返回 None（只跑 ALWAYS）。"""
    text = normalize_path(path)
    if text == "":
        return None
    for glob, targets in PATH_RULES:
        if fnmatchcase(text, glob):
            return glob, targets
    return None


def matches(name: str, target: str) -> bool:
    return name == target or name.startswith(target)


def _wanted_targets(changed_paths: list[str]) -> tuple[set[str], bool]:
    targets: set[str] = set(ALWAYS)
    for path in changed_paths:
        hit = rule_for(path)
        if hit is None:
            continue
        if hit[1] == ALL:
            return targets, True
        targets.update(hit[1])
    return targets, False


def select_changed(changed_paths: list[str], catalog_names: list[str]) -> list[str]:
    """改动路径 → 按用例名单顺序排好的用例名。总含 health、login（名单里有的话）。"""
    targets, run_all = _wanted_targets(list(changed_paths))
    if run_all:
        return list(catalog_names)
    return [name for name in catalog_names if any(matches(name, target) for target in targets)]


def explain(changed_paths: list[str]) -> list[tuple[str, str, str]]:
    """每条改动路径命中哪条规则、选了什么，给命令行打印到 stderr。"""
    rows = []
    for path in changed_paths:
        hit = rule_for(path)
        if hit is None:
            rows.append((path, "", "（表外，只跑 ALWAYS）"))
        elif hit[1] == ALL:
            rows.append((path, hit[0], "全部"))
        else:
            rows.append((path, hit[0], ",".join(hit[1]) or "（只跑 ALWAYS）"))
    return rows
