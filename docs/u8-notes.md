# U8 行为说明

本文记录桥所依赖的 U8+ V18.0 行为：业务组件的调用形状、按引用参数、必填字段、写入的表、事务行为和已知陷阱。除另行注明外，各条均已在测试账套实测。升级 U8 或打补丁后，先在测试账套上复核（见 `CONTRIBUTING.md`）。

本文不是用友官方文档。组件名、方法名和参数来自已安装 U8 的运行时行为，官方未承诺其稳定性。

## 1. 名词

| 名字 | 说明 |
| --- | --- |
| CO（业务 COM 组件） | 各业务模块的 32 位 VB6 进程内 COM，如 `VoucherCO_Sa.ClsVoucherCO_SA`（销售）、`VoucherCO_PU.clsVoucherCO_PU`（采购）、`USERPCO.VoucherCO`（库存）、`UFAPBO`（应收应付）。数据以 ADO 行集持久化的 XML DOM 传递，即二次开发常说的「CO 接口」 |
| U8 API | 官方 .NET 框架：`U8EnvContext` + `U8ApiComBroker` + `U8API/<对象>/<动作>`。本项目只在生产订单审核上使用 |
| EAI | U8 的 XML 导入导出（`ufinterface` 报文）。本项目用于导入总账凭证和基础档案 |
| OpenAPI | 用友开放平台云端接口，需单独购买开通。本项目不使用 |

这些组件只能在装有同版本 U8 的 Windows 32 位进程里调用。许可问题见 `faq.md`。

## 2. 登录

```
U8Login.clsLogin.Login(子系统, 账套, 年度, 操作员, 口令, 日期 yyyy-MM-dd, 应用服务器, "")
```

- 返回 false 时读 `ShareString` 取原因；返回 true 后还要求 `LogState` 为字符串 `"0"`。
- 年度是账套库 `UFDATA_<账套>_<年度>` 的年度，不是当前会计年度。
- 应用服务器填 U8 注册的应用服务器地址。填 `127.0.0.1` 有时取不到数据源，此时改用服务器在 U8 中登记的名字或地址。
- 登录成功后：`UfDbName` 是 OLE DB 连接串（与 U8 客户端相同），`cUserName` 是操作员姓名，`userToken` 是审批用令牌 XML（含 `cEmployeeId`，即操作员关联的人员编码）。
- 登录会在 `UFSystem` 中留下登录记录和站点计数。
- 用完在同一线程上 `ShutDown()`。

子系统：销售和质量 `SA`，库存 `ST`，采购 `PU`，应收 `AR`，应付 `AP`，总账 `GL`，基础档案 `AS`，生产订单审核、新增、删除 `MO`。

### 许可点数（加密点数）

「加密点数已饱和」表示加密服务器（许可服务器）拒绝了本次登录要占用的点。U8 按「工作站 × 子系统」占点，同一产品包内的模块共用该包的点数，计数方式以 U8「许可管理」显示的为准。本项目经 U8 业务组件登录，与 U8 客户端一样占用许可点数。

- 加密服务器地址：桥的 `licenseServer` 为空时，取 U8 应用服务器配置（`<u8Home>\AppServer\UFSoft.U8.Framework.Login.BO.config`）中登记的许可服务器，即 U8 自身使用的地址。
- 点数采样：可用时，桥经 U8 自带的本机许可客户端库读取当前已用和总数，按产品包统计，结果与「许可管理」一致（健康检查 `license_source` 为 `leases`）。库不可用、读不到或 `licenseLeases` 为 `false` 时，回落为统计 `UFSYSTEM..UA_TaskLog` 中登记的工作站数（`license_source` 为 `tasklog`）。`UA_TaskLog` 只列已登记的任务，许可服务器仍持有的点可能不在其中，因此回落时的数字只是下限。
- 状态：登录因饱和失败、重试后仍失败时返回 503 `u8_license_full`；已用接近总数时健康检查 `license` 为 `near`（阈值见 `licenseWarnFree`）。包内各模块显示整个包的数，因此某个模块「已饱和」即其所在的包已满。
- 配置项 `licenseRetries`、`licenseSampleMinutes`、`licenseLimits`、`licenseWarnFree`、`licenseServer`、`licenseLeases` 的含义见 [配置](configuration.md)。

## 3. COM 调用通则

### 按引用参数

- 尾部可选的 ByRef 参数不传。传 `Type.Missing` 会 `DISP_E_TYPEMISMATCH`（如 `USERPCO.Verify` 的 `MakeWheres`、`VoucherCO_Sa.Save` 的 `DomConfig`、`GetVoucherNO` 的 `DomFormat`）。
- 必须占位而调用方不用的 ByRef 对象槽，传 `new System.Runtime.InteropServices.DispatchWrapper(null)`；传普通 `null` 会 `DISP_E_TYPEMISMATCH`。
- 晚绑定调用只把契约中的下标标为 by-ref，不多标、不漏标：

| 调用 | by-ref 下标 |
| --- | --- |
| `VoucherCO_Sa.Save(头, 体, 模式, vNewID)` | `{3}` |
| `VoucherCO_PU.VoucherSave2(头, 体, 模式, 主键)` | `{3}` |
| `Delete(头, 体)`（销售、采购） | `{0,1}` |
| 销售订单 `OrderClose(头, bClose)` / `OrderClose(头, bClose, iSOsID)` | `{0,1}` / `{0,1,2}` |
| 销售 `GetVoucherNO(头, no, err)` | `{0,1,2}` |
| 采购 `GetVoucherNO(头, 卡片, err, no)` | `{0,1,2,3}` |
| 库存 `Update`（12 个参数） | `{4,5,6,7,8}` |
| 库存 `MakeOutVouch` | `{1,2,3,4,5}` |
| 调拨单 `Verify`（12 个参数） | `{2,3,4,5,6,7,8,10,11}` |
| 采购 `ClosePOItems` / `OpenPOItems` | `{2,3}` |
| 到货单 `CloseArrItems` / `OpenArrItems` | `{2}`（第 4 个参数 `bleCloseAll` 按值） |
| 采购 `GetVoucherDataById(头, 体, "", id, "")` | `{0,1,4}` |
| 销售发票 `VerifyVouch(头, bool)` | `{0,1}` |

- `BodyCheck` 是普通调用，不加 by-ref；加了会把 DOM 换成无效对象。

### 签名自检

- 上表及其余调用点的实参个数、by-ref 下标汇总在 `co/bridge/src/SigTable.cs`，改调用点时同步修改。`--check-signatures` 按它读运行时类型库核对。
- VB6 类型库中 ByRef 参数标 `[in, out]`；`IXMLDOMDocument2*` 之类接口指针本身按值。自检把 `[out]`、`VARIANT*` / `BSTR*` / `IFoo**` 视为按引用。
- 与类型库不一致、以实测为准的：采购 `Delete` 的头、体在类型库中是 `[in]`，桥仍按 `{0,1}` 传；采购 `GetDefaultVTID` 的连接参数在类型库中是 `[out]`，桥按值传；调拨 `Verify` 第 9 个参数 `MakeWheres` 是 `out` 可选，桥传 `DispatchWrapper(null)` 且不标 by-ref。
- `ussaserver.dll`、`voucherco_sa.dll`、`U8Login.dll`、`Info_PU.dll` 等未登记可读的类型库，表中这些行不核对可选尾参数个数；U8 API 框架和审批代理是 .NET 组件，一般无类型库，只确认已注册。

### DOM

- 数据是 ADO `Recordset.Save(dom, 1)`（`adPersistXML`）存入 `MSXML2.DOMDocument` 的行集 XML。行为 `//rs:data/z:row`，列为属性。
- MSXML 3 缺省使用 XSLPattern。要用 `local-name()` 等 XPath 函数，先 `setProperty("SelectionLanguage","XPath")` 并声明 `rs`、`z` 命名空间。属性名区分大小写，按该行集 schema 的写法（如 `SaleOrderQ` 中为小写 `cmaker`）。
- `GetDefaultVoucherDom`、`GetVouchData` 的空白模板只有 schema、没有 `z:row`，需自行 `createNode(1,"z:row","#RowsetSchema")` 挂到 `//rs:data`。也可用 `select '' as editprop, … where 1=2` 再 `Recordset.Save` 得到空白 DOM，然后补行。
- 表头 `z:row` 上主键属性必须存在，值可为空串（销售发票 `sbvid=""`、到货单 `id=""`）。缺失时 U8 回「使用 Null 无效」。
- 行状态写在 `editprop`：新增 `A`，修改 `M`，删除 `D`（节点保留），未改动为空串。新行去掉行主键，写 `irowno`。
- 会 `FinalReleaseComObject` 的辅助函数返回后，之前取得的 `z:row` 引用全部失效，需重新选取。
- 新增行可 `cloneNode(true)` 一行已加载的行得到合法节点，但不得留下种子行数据：只保留外键、`irowno`、`editprop`、本次要写的字段和算出的单位、金额。
- 修改人、修改日期等戳记，schema 中没有该属性就不要设。

### 时间戳 `ufts`

- `ufts` 取 `CONVERT(CHAR, CONVERT(MONEY, UFTS), 2)` 的原值，左侧带空格，只能去掉右侧空格。去掉左侧空格后 U8 报「单据已经被其他人修改或删除」。

### 不要用的调用

| 调用 | 原因 |
| --- | --- |
| `MakeVouchByWhere` | 各种参数形状均返回「类型不匹配」。销售出库用 `MakeOutVouch` |
| `AutoAddBodyRecord`（UFAPBO） | 报「未设置对象变量」 |
| `VouchCanSign`（UFAPBO） | 对已制单、已核销只回笼统的失败原因。改为调用前自行查库 |
| `SubmitApplicationMessage2`（审批代理） | 能建流程实例，但不改单据的审批状态 |
| `GetUnAuditTasks`、`GetTasks2` | 读的是消息中心提醒，有待办时也返回空。待办直接查 `Table_Task` |
| EAI `proc="delete"`（总账） | U8 回「暂不提供此项功能」 |
| EAI `proc="edit"`（档案） | 整张替换，需重新校验全部必填 |
| EAI `proc="Query"` | 把结果文件写进 U8 安装目录的 `Logs` |

## 4. 金额

CO 的 `Save`、`VoucherSave2`、`Insert`、`Update` 都不重算价税，金额和辅数量由调用方（桥）填好。

### 销售：单行 `BodyCheck`

`BodyCheck(键, 表体, 表头, 1)` 只能对单行表体调用；多行时 U8 读第 R 行、写第 0 行。做法：

1. `cloneNode(true)` 复制表体 DOM，删掉其他 `z:row`，写上要触发的属性。
2. 普通调用 `BodyCheck`。返回空串为成功，非空为 U8 原文。
3. 把临时行的全部属性拷回真实行。

一行的顺序：先 `iquantity`，再调用方给的单价键（有 `itaxunitprice` 用它，否则 `iunitprice`），调用方改了税率才调 `itaxrate`，然后再调一次单价键。`iquantity` 重算件数（数量 / 换算率）和扣率；行上已有单价时还重算无税单价、金额、税额、价税合计和本币各列。

单价键（`itaxunitprice`、`iunitprice`、`itaxrate`）的 `BodyCheck` 有时不改行，而是返回 `<R K="…" V="…"/>`，需把这些 K/V 写回真实行。

例：数量 100、含税单价 11、税率 13%，得到无税单价 9.7345、金额 973.45、税额 126.55、价税合计 1100。

参照生单时，先按本次数量 / 来源数量缩放来源行的金额列（保留 2 位），件数 = 数量 / 换算率，再跑一次单行 `BodyCheck("iquantity")`。

### 采购

采购的 `BodyCheck`、`BodyCheckEx` 不算金额。单价 6 位、金额 2 位，舍入 `AwayFromZero`。`r` 为行税率 `ipertaxrate / 100`，`e` 为表头汇率 `nflat`。

| 行上 `btaxcost` | 算法 |
| --- | --- |
| 1（给了含税单价） | `isum = round(数量 × itaxprice, 2)`；`imoney = round(isum / (1+r), 2)`；`itax = isum − imoney`；`iunitprice = round(itaxprice / (1+r), 6)` |
| 0（给了无税单价） | `imoney = round(数量 × iunitprice, 2)`；`itax = round(imoney × r, 2)`；`isum = imoney + itax`；`itaxprice = round(iunitprice × (1+r), 6)` |

本币：`inatunitprice = round(iunitprice × e, 6)`，`inatmoney`、`inattax`、`inatsum` 同样乘 `e`，保留 2 位。

### 库存

其他入库、其他出库按调用方本次送来的字段：只送 `iPrice` 时反算 `iUnitCost = round(iPrice / 数量, 6)`；否则行上有单位成本时 `iPrice = round(数量 × iUnitCost, 2)`。调拨单数量变化且行上已有 `iTVACost` / `iTVPCost` 时重算对应金额。

有计量单位组（`Inventory.iGroupType <> 0`）的存货必须带辅计量：辅计量单位（调用方给的，否则存货的库存辅计量，否则主辅计量）、换算率（`ComputationUnit.iChangRate`）、件数 = `round(数量 / 换算率, 6)`。缺失时 U8 返回「固定换算率的存货[x]的辅计量单位不允许为空!」。改了数量而调用方未送件数时按换算率重算件数；换存货或换辅计量单位时，先清掉已加载的单位、换算率和件数再重填。

## 5. 应收应付（UFAPBO）

类型由 `cFlag` 和 `cVouchType` 区分：收款单 `Ap_CloseBill` 上 `AR`/`48`，付款单 `AP`/`49`，应收单 `Ap_Vouch` 上 `AR`/`R0`，应付单 `AP`/`P0`。同表还有供应商退款（`AP`/`48`，卡片 `AP48`，`ap_refund`）和客户退款（`AR`/`49`，卡片 `AR49`，`ar_refund`），组件和调用同收付款单，模板条件按本类型发（应付下 `cVouchType='48'`、应收下 `'49'`；48 / 49 与 AR / AP 不是一一对应）。

| 操作 | 调用 | 说明 |
| --- | --- | --- |
| 初始化 | `clsAccount_AP.Init(login, "AR"或"AP")`；收付款单 `clsCloseBill.Init(login, conn, acc, 子系统)` by-ref `{0,1,2,3}`；应收应付单 `clsAPVouch.Init(login, conn, acc)` by-ref `{0,1,2}` | `clsAccount_AP.Init` 返回 false 也可用，不看返回值 |
| 读取 | `GetVouchData(条件, 头, 体, vt, err)` by-ref `{1,2,3,4}`。收付款单条件 `<condition keytype='1' iID='…'/>`；应收应付单 `<condition keytype='1' cLink='…'/>`，`cLink` = `cVouchType + cVouchID` | |
| 空白模板 | `GetVouchData("<condition cVouchType='48'/>", …)`（应收应付单用 `R0` / `P0`） | 只有 schema，表头表体行都自行挂 |
| 新增 | `SaveVouch(头, 体, err, true)`。收付款单 by-ref `{0,1,2}`；应收应付单两个 DOM 按值，只有 `{2}` | U8 把新主键和单号写回表头 DOM。在请求连接的事务里可回滚，但单号记录（`VoucherHistory`）回滚后不退，只留断号 |
| 审核 | 收付款单 `Sign("<condition type='0' iID='…'/>", msg)`；应收应付单 `Sign("<condition type='0' cLink='…' cVouchType='…' cVouchID='…'/>", msg)`。by-ref `{1}` | 写审核人 `cCheckMan`、审核日期，并写往来明细 `Ar_Detail` / `Ap_Detail` |
| 弃审 | 收付款单 `CancelSign("<condition type='0' cVouchType='48' cVouchID='…' iID='…'/>", msg)`；应收应付单同审核条件。by-ref `{1}` | 删除往来明细 |
| 删除 | 收付款单 `DeleteVouch("<condition keytype='2' iID='…'/>", err)`；应收应付单 `<condition keytype='2' cVouchType='…' cVouchID='…'/>`。by-ref `{1}` | 尾部可选的 `Ufts` 不传 |
| 修改 | `GetVouchData` 读入，改表头和表体（改的行 `editprop=M`），`SaveVouch(头, 体, "", false)`，by-ref 同新增 | 返回 true，留在请求连接的事务里；不写往来明细，不写修改人 |

- 条件只发单张 `type='0'`。`type='1'` 是按日期批量审核，不要发。
- 退款单金额填正数，与收付款单相同；`Sign` 时 U8 在 `Ap_Detail` / `Ar_Detail` 写**负数**登记行（应付的收款单冲减应付、应收的付款单冲减应收），与 U8 客户端录入的退款单一致。核销视图、单据追溯中往来明细的类型按（`cFlag`, `cVouchType`）区分：应收 48 收款单、应收 49 客户退款，应付 49 付款单、应付 48 供应商退款。
- 应收单、应付单每一行表体都要带 `cexch_name` 和 `iExchRate`，只写在表头上保存会失败。
- 外币应收单、应付单的行币种跟科目，不跟表头。表体科目不核算外币（`code.cexch_name` 为空）的行写表头外币，U8 回「第1行的币种与科目不一致」。U8 客户端录入的外币单据，收入、费用、税金科目行存 `cexch_name=人民币`、`iExchRate=1`、`iAmount_f = iAmount` = 本币（`iTax`、`iNoTaxAmount_f` 同本币列）；科目核算币种等于表头币种的行保留表头币种、汇率和原币。`bexchange` 是期末调汇标志，不表示核算外币，不参与判断。
- 外币应收单、应付单保存时 U8 同时检查两条，任一不符即回「借贷不平，不能保存。」：表头本币 = 各行本币之和，且表头本币 = `round(表头原币 × 汇率, 2)`（远离零取整，精确相等）。桥在两者有差额时把差额并到一行记本位币的行，没有可调的行则 400。
- 表头金额 = 行金额合计，新单的未核销金额等于金额。应收单表头 `bd_c=True`、表体 `False`，应付单相反。行金额含税：不含税 = `round(金额 / (1 + 税率/100), 2)`，税额 = 金额 − 不含税，原币和本币各算一次。各行税率相同时写到表头，否则表头为 0。
- 收付款单表头 `cCode` 是结算科目（存在且末级），表体 `cKm` 必须是本系统受控科目；应收应付单表头 `cCode` 必须是本系统受控科目，表体 `cCode` 不能是应收 / 应付受控科目。受控看科目表的 `cother`。
- 票据登记生成的收款单（`cSrcFlag`=C、`cCoVouchType`=50）在票据无处理记录、余额等于票面、无换票时可以弃审；修改、删除仍归票据删除。
- 汇兑损益的发票累计回写：新增时销售发票走 `Ussaupdispatch.clsWrite2Bill.UpdateBillForAR`，采购发票由 U8 直接改 `PurBillVouchs.iTotal`；取消时采购发票调 `Pu_Productinf.cls_ForAPsrv.UpdateBillForAP(ByRef DBconn As Connection, ByVal ctablename As String) As String`（by-ref `{0}`，返回空串为成功），临时表 `#ap_purbillHXdata`（`autoid`、`iexchsum`、`imoneysum`），回写 `PurBillVouchs.iOriTotal` / `iTotal`。采购一侧未经实测；桥调用后按 U8 公式核对，不符回滚 409。
- 能否弃审、删除，调用前查库：凭证号（`cPzID`）、是否手工单据（`cCoVouchType`、期初）、是否来自票据或网银（`cSrcFlag`、`cNoteNo`、`bFromBank` / `bToBank`）、核销（余额不等于金额或有核销人）、往来明细、`GL_mend` 的 `bflag_AR` / `bflag_AP` 结账标志。

### 票据登记：分包票据

分包票据（`AP_Note.bsubpackage=1`）与非分包票据的登记只有以下差别，其余列、收款单表体（`iType=0`、客户控制科目、金额 = 票面）、往来明细、条码 `csysbarcode`（不带区间）都相同：

- `AP_Note`：`bsubpackage=1`，子票区间 `csubnostart` / `csubnoend`（bigint；非分包为 NULL、`bsubpackage=0`）。号多为银行给的 9 到 10 位号，不一定从 1 开始；每个号 0.01 元，`(止 − 起 + 1) = 票面 × 100`。30 位的 `cVouchID` 是票据包号，与是否分包无关；`iChangeType` 是换票，不是新一代票据标志。
- `Ap_Note_AvailRange`（`AutoID`、`cNoteLink`、`cavailstart`、`cavailend`、`iavailamount`、`iavailamount_local`）：登记时写一行整段可用区间，`cNoteLink` = `cLink`，起止 = 子票区间，两列金额 = 票面。无外键，`AP_Note` 上无触发器，删票据时需自行删除。票据处理时按剩余区间重写，全部用完不留行；非分包票据无行。
- 收款单（48）：`cNoteNo` = `cCoVouchID` = `票据号-起-止`（通常不补零，偶见补零到 12 位）；非分包即票据号。
- 登记不写 `AP_Note_Sub`、`Ap_Note_HPDetail`、`AP_PayDetail`。U8 只校验子票区间的起止是否输入、是否合法，不校验区间与金额一致；桥在登记前 400（起止为 1 到 15 位正整数；区间不符时为「子票区间与金额不符：区间 N 张 = X.XX 元，票面 Y.YY（每张子票 0.01 元）」）。
- 票据对应的收款单按 `AP_Note.iCloseID`（= 收款单 `iID`）找，不按票据号；分包票据的后缀起止按数值比较（补零的也认）。登记查重时，收款单票据号为「票据号-起-止」也算已被引用（候选超过 200 张时 409）。删除票据同时删其可用区间，前提是可用区间仍为登记时的整段。

### 票据退回 9C（`notes/process` 的 `return`）

U8 的退回先 `Ap_Proc_CancelNo`（`CL` / `AR`，处理号 `CLAR` 补零到 17 位，如 `CLAR0000000000001`），再经 `UFAPBO.clsAPVouch.SaveVouch` 存一张应收单，然后写处理行和往来明细。桥同样经 `clsAPVouch.SaveVouch`（与 `vouchers/create` 的 `ar_bill` 同一路径；组件在开事务之前打开并取模板，保存在请求连接的事务里）生成应收单，再补写其余列、写处理行和往来明细，全部在一个事务里。`Ap_Vouch.Auto_ID` 不是自增列也无缺省值，主键由 U8 组件分配，桥不能自行 INSERT 应收单。

- 登记时的收款单（48）不动：不红冲、不改金额和余额，审核行照旧；之后的核销（`9P`）把新应收单作为对方单据挂在 48 上。
- 应收单（`Ap_Vouch` / `Ap_Vouchs`，`R0`）：`dVouchDate` = 退回日期，`cDwCode` = 票据的 `cEndorser`，部门、业务员取票据；表头摘要「转出票据{票据号}」（不带子票区间），表体一行摘要「票据转出」；表头 `cCode` = 应收控制科目（取登记收款单应收款行的 `cKm`，没有时取基本科目 `kzkm`），表体不给科目；金额 = 本次金额，税率 0，`bd_c` 表头 1、表体 0，汇率 1。`Auto_ID`、单号（`YS` + 日期 + 流水）、`cLink` 由 U8 分配（桥优先取组件写回表头 DOM 的值）。保存后在同一事务里补写 `SaveVouch` 不写的列：`cCoVouchType=50`、`cSrcNo` = 票据号、`iRAmount` = `iRAmount_f` = 0，审核列 `cCheckMan` = 操作员、`dverifydate` = 退回日期、`dverifysystime` = 当前时间；`dcreatesystime` 为空时补当前时间，`VT_ID` 为空时补卡片 `AR04` 的缺省模板，`csysbarcode` 为空时补「`||arr0|`单号」。应收单由此处于已审核状态，但没有 `cProcStyle=R0` 的审核行；`vouchers/verify` 对这类应收单（`cCoVouchType` 非空）的审核、弃审一律 409「单据由票据退回等处理生成，不能审核或弃审」，只能经取消处理撤销。
- `AP_Note_Sub`：`cProcStyle=9C`，`iAmount` = 本位币金额，`iExpense` / `iIntrest` = 0，`cBank` 空，`cCode` = 应收控制科目，`cCoVouchType=R0`、`cCoVouchID` = 应收单号（结算、贴现、背书写的 `cCoVouchType=48` 只是标记），分包票据带 `csbnstart` / `csbnend`。
- 票据 `iRAmount` / `iRAmount_Local` 减本次金额；分包票据重写 `Ap_Note_AvailRange`（同结算）。分包票据只能一次退回全部可用子票区间（票据有了 9C 后不能再做其他处理，部分退回会使剩余区间无法处理），否则 409「分包票据退回须退回全部可用区间…」；可用区间不止一段时请在 U8 客户端处理。
- `Ar_Detail` 两行，同处理号、`cProcStyle=9C`：应收单行 `cVouchType` = `cCoVouchType` = `R0`、借方、`iFlag=0`、`iExchRate=1`、`csign=Z`、科目 = 应收控制科目、带部门业务员、`dVouchDate` = `dRegDate` = 退回日期、`cCheckMan` = 操作员；票据行 `50` 贷方、`iFlag=3`、`iExchRate=0`、科目 = 票据科目、`cVouchID` = 票据号（分包票据为「票据号-起-止」，不补零）、`dVouchDate` = 票据签发日期。两行摘要相同，缺省为「退回」+ 客户名称 +「电子承兑」，调用方可给 `digest`。
- 检查：日期在会计年度内、不早于签发和收票日期及该票据已有的处理（不含 `*L`、`H*` 行）、所在期间未结账；金额大于 0 且不超过余额；已有 9C 的票据不再处理。`SaveVouch` 前后 `@@TRANCOUNT` 改变返回 504，U8 返回 false 时 409 `u8_rejected` 带原文。
- 制单（`arap/process/voucher`，PJ）：应收控制科目贷 −金额、票据科目借 −金额。取消（`arap/process/cancel`）删往来明细、处理行和应收单，加回余额和可用区间；应收单已被本批以外的往来明细引用（核销、转账等）时拒绝。不写 `9CL` 保留线索行。

### 应付票据（`flag` `AP`：登记、删除、结算 9A、退回 9C）

应付票据与应收票据由同一套 U8 逻辑处理，`cFlag` 在运行时决定 48 / 49、`Ar_Detail` / `Ap_Detail`。桥只实现与应收票据对称的部分，属第二级写入（复现 U8 界面的 SQL）：缺省关闭，`enableReplicatedWrites` 打开后也只对 `testAccounts` 中的账套开放（登录前和入队后各查一次；处理制单、取消处理、删除同样），见 `configuration.md`。`AccInformation` 的票据科目 `pjkm` 应付一侧（`cApCode`）可能为空，此时请求必须给 `note_km`。

- 登记：票据行同应收，`cFlag=AP`、`cLink` = `AP50` + 票据号、`csysbarcode` = `||ap50|` + 票据号、`cEndorser` = 供应商（请求的 `vendor`）、`cEndorserName` = 供应商名称；票据科目取请求的 `note_km`，否则 `pjkm` 的 `cApCode`。付款单（49）经付款单新增的同一路径（`ap_payment`，`clsCloseBill.SaveVouch`，模板取卡片 `RP02` 的缺省模板），表体一行 `iType=0`、科目 = 应付控制科目（`km` 或 `kzkm` 的 `cApCode`），表头科目（票据科目）在 DOM 中去掉、保存后补写；补写和解除的条件为 `cVouchType=N'49'`。
- 删除：同应收，删付款单（`clsCloseBill.DeleteVouch`）。
- 结算 9A（`PJJAP`）：处理行同应收（`cCoVouchType` 写标记 48），`Ap_Detail` 的票据 50 行记借方 `iDAmount`（应收为贷方）。制单：票据行借应付票据、银行行贷方，缺省凭证类别「付」。
- 退回 9C（`CLAP`）：同应收经 `clsAPVouch.SaveVouch`（`ap_bill`）生成应付单 `P0`（主键、单号由 U8 分配；保存后补写标记和审核列，`VT_ID` 为空时补卡片 `AP04` 的缺省模板，`csysbarcode` 为空时补「`||app0|`单号」），表头 `bd_c=0`、表体 1，表头科目 = 登记付款单应付款行的科目，没有时取应付 `kzkm`；处理行 `cCoVouchType=P0`；`Ap_Detail` 两行：应付单行贷方 `iCAmount`（`iFlag=0`、`iExchRate=1`、`csign=Z`），票据行借方。制单同应收退回换方向取负：应付控制科目借 −金额、应付票据贷 −金额。取消按处理行的 `cCoVouchType` / `cCoVouchID` 删应付单。
- `Ap_CancelNo` 没有 `PJJ` / `CL` + `AP` 的行时，桥在取号前补一行 0，`Ap_Proc_CancelNo` 由此编出 `PJJAP…`、`CLAP…`。未验证：这样编出的处理号、`P0` 单号与 U8 客户端处理时是否一致；退回的制单。
- 不支持：应付票据的贴现 9D、背书 9E，以及 `PJTAP` / `PJBAP` 的取消、制单，请求 400。
- 功能权限：`AP0504`（票据录入）/ `AP2403`（票据删除）。

### 发票的应付审核、应收审核

采购发票在应付款管理中的审核（`cPBVVerifier`）、销售发票在应收款管理中的审核（`cVerifier`）不走 `clsAPVouch`，而是 `UFAPBO.clsPub_AP`，登录子系统 `AP` / `AR`。U8 界面的封装（`UFAPSuite.clsPuAP.Sign_PurBill` / `clsSaAR.Sign_SaleBill`）自开连接和事务，桥不用它，直接调 `UFAPBO`：

| 步骤 | 调用 | 说明 |
| --- | --- | --- |
| 初始化 | `acc = clsAccount_AP`，`acc.Init(login, "AP"或"AR")` 按值，不看返回值；`pub = clsPub_AP`，`pub.Init(login, conn, acc, "AP"或"AR")` by-ref `{0,1,2,3}`，`conn` 为请求连接 | 省略 `conn` 报 `DISP_E_PARAMNOTFOUND`；传请求连接使审核落在桥的事务里 |
| 预检 | 采购 `PUVouchCanSign("01"或"02", cPBVCode, bCancel, msg)`，销售 `SAVouchCanSign("26"或"27", cSBVCode, bCancel, msg)`，by-ref `{3}`，`msg` 以空串起 | true 可审核；false 时 `msg` 为原因。弃审时 `bCancel=true` |
| 审核 | `Sign_PurBill(条件, msg)` / `Sign_SaleBill(条件, msg)`，by-ref `{1}`，`msg` 以空串起 | true 为成功；false 时 `msg` 为 U8 原文 |
| 弃审 | `CancelSign_PurBill(条件, msg)` / `CancelSign_SaleBill(条件, msg)`，同一条件，by-ref `{1}` | 清审核人、审核日期，删除原始往来明细行 |

- 条件：采购 `<condition type='0' PBVID='<PBVID>' cVouchType='01' cVouchID='<cPBVCode>' bneedcheck='1'/>`；销售 `<condition type='0' SBVID='<SBVID>' cVouchType='26' cVouchID='<cSBVCode>' bneedcheck='1'/>`。属性名区分大小写，值需转义；不带 `cLink`、`Ufts`、`bFirst`。
- 条件中缺 `PBVID`（销售 `SBVID`）时报「使用 Null 无效」（VB6 运行时错误 94），发生在任何 SQL 之前。`msg` 以 Null 起也可能报 94，一律以空串起。不要预先创建 `Info_PU` / `VoucherCO_PU`。
- 事务：`Sign_PurBill` / `CancelSign_PurBill` 在请求连接的事务里执行（`@@TRANCOUNT` 为 1），回滚可撤销全部改动。桥照常包 `CoTrans`，提交前核对审核人和登记行，提交后在新连接上回读。
- 采购审核写：`PurBillVouch.cPBVVerifier` = 操作员姓名、`dverifydate` = 登录日期；每张发票一条 `Ap_Detail`（`cFlag=AP`，`cVouchType` = `cProcStyle` = `01`/`02`，`cCancelNo` = `JZ` + 类型 + 单号，`iCAmount` = 金额，`cSign=Z`，`iPeriod` = 登录月份，`cCheckMan` = 操作员姓名，`cPZid` 空）。不生成凭证，也不自动核销。已应付审核的发票表体都有 `PurBillVouchs.cClue`（AP 线索号），U8 的条件另带 `cClue Is Null`。
- 销售审核写 `SaleBillVouch.cVerifier`（应收审核人；`cChecker` 是销售复核）、`dArverifydate`、`dArverifysystime` 和 `Ar_Detail` 行。
- 桥调用前查：已复核（采购 `cVerifier`、销售 `cChecker`）、未审核、`IsWfControlled` / `iswfcontrolled` 为 0、采购 `iNetLock` 为 0、表体无 `cClue`、登录日期所在年月 `GL_mend.bflag_AP` / `bflag_AR` 未结账；弃审另查：往来明细无 `cPZid`、无 `cProcStyle` 不等于单据类型的行、无以本单为对方单据（`cCoVouchType` / `cCoVouchID`）的行、审核登记行所在期间未结账。
- 往来科目：直接调 `Sign_*` 时原始往来明细行的 `cCode` 为空，而 U8 客户端审核时该列为往来控制科目。桥在同一事务里逐行调 `clsPub_AP.CusVenToCtrlKMForSAPU(往来单位, 币种, 销售 / 采购类型, 存货, "AR"|"AP")`（按值 5 个参数）补 `cCode`；销售 / 采购类型取 `SaleBillVouch.cVouchType` / `PurBillVouch.cPBVBillType`。返回空则整笔回滚，409「取不到往来单位 … 的往来控制科目，请先在 U8 应收应付的科目设置里设好」，不猜科目。
- 功能权限：`AP050104` / `AR050104`。

### 核销（`U8ApCancel.cLsCancel`）

应收（应付）款管理的手工核销：收款单（付款单）对销售发票、应收单（采购发票、应付单）。组件为 VB6 的 `U8ApCancel.cLsCancel`（`ufcomsql` 目录），U8 界面和信用证模块都是拼一段 XML 交给它的 `Save`。.NET 侧无核销接口。

| 步骤 | 调用 | 说明 |
| --- | --- | --- |
| 初始化 | `hx.Init(login, "AR"或"AP", conn)` by-ref `{0,2}`，`conn` 为请求连接 | 返回 true。第二个参数只能是 `AR` / `AP`，不要传 `clsAccount_AP` |
| 核销 | `hx.Save(xml)` by-ref `{0}` | true 为成功。执行后参数被改写为 `<root/>` |

- 调用前不设任何属性（`dDate`、`cRPFlag`、`cSelectCols`、`bVouStyleZb` 都不设）：`Save` 自己从报文读 `bsamehx`、`cdwcode`、`crpflag`、`dhxdate`。把 `dDate` 按字符串设会在赋值时报 VB 错误 13。
- `hx.cCancelNo` 不可读（`DISP_E_UNKNOWNNAME`）。新核销号从 `Save` 之后新写的往来明细行取（`Auto_ID` 大于调用前最大值、`cProcStyle='9P'`、`cVouchType` / `cVouchID` 为收付款单）。
- 事务：`Save` 在 `Init` 给的连接上执行，不自行 `BeginTrans`（`@@TRANCOUNT` 为 1，回滚撤销全部改动）。桥照常包 `CoTrans`。核销号计数（`Ap_CancelNo`）回滚后可能不退，只留断号。
- 以 `AR` / `AP` 登录时，`Save` 不查登录日期、期间是否结账、单据锁（这组检查只在登录子系统为 `PO` 时启用）。桥自行检查：单据已审核、往来单位一致、余额够、核销日期不早于单据日期和应收（应付）启用日期（`AccInformation` 的 `dARStartDate` / `dAPStartDate`）、审批流和采购发票网络锁、核销日期所在期间未结账。不检查 `LockVouch` 表。
- 期间：`Save` 用 `SELECT iId, iYear FROM UA_Period WHERE cAcc_id=… AND dBegin<=日期 AND dEnd>=日期 AND (bIsDelete=0 or bisdelete is null)` 确定期间号，写入 9P 行的 `iPeriod`。会计期间不一定是自然月；桥同样按 `UFSYSTEM..UA_Period` 找期间，再查 `GL_mend` 该期的 `bflag_AR` / `bflag_AP`。
- 余额口径（与 `Save` 相同）：`Ar_Detail` / `Ap_Detail` 上 `cCoVouchType`、`cCoVouchID` 为被核销单据，`cDwCode` 为往来单位，`iBVid` 为行，`iFlag<3`（`iFlag` 为 NULL 的行不算，不要写成 `isnull(iFlag,0)<3`），`sum(iDAmount_f-iCAmount_f)`（应付相反）。被核销单据必须有原始行（已应收 / 应付审核）且余额不为 0。发票原始行的 `iBVid` 是 `SaleBillVouchs.AutoID` / `PurBillVouchs.ID`；应收单、应付单原始行 `iBVid` 为 0，按整单核销。
- 应付的往来单位看往来明细的 `cDwCode`，不看采购发票表头 `cVenCode`：开票单位 `cUnitCode` 可能与 `cVenCode` 不同，往来明细记的是 `cUnitCode`。

报文用 DOM 生成：

```xml
<canceldata cdwcode="C900001" crpflag="AR" dhxdate="2026-01-28" bsamehx="True">
  <close cdwcode="C900001" cexchname="人民币" cjsdnum="SK0000000001" crpflag="AR" djsddate="2026-01-05"
         bprepay="0" iexchrate="1" id="<Ap_CloseBills.ID>" ijsdhxamount_f="6.00" ijsdhxamount="6.00">
    <vouch cvouchtype="26" cvouchcode="SOZP0000000001" dvouchdate="2026-01-07" cinvcode="" cdigest=""
           iinvid="<iBVid>" cexchname="人民币" iexchrate="1" icancelamount_f="6.00" icancelamount="6.00"
           bcancelall="false"/>
  </close>
</canceldata>
```

- `vouch` 必须是 `close` 的子节点，每个被核销单据行一个。写成平级时 `Save` 找不到 `vouch`，会走「收付款单自己核销自己」的分支。
- `close` 的 `id` 是收付款单表体行 `Ap_CloseBills.ID`，不是表头 `iID`；`Save` 按它重读收付款单行，其余收付款单字段以库为准。`ijsdhxamount*` 是该行本次核销合计。
- `bcancelall` 必须带（小写 `false`），缺失报 VB 错误 13。数值、日期、布尔属性缺失同样报 13（缺失属性被转成空串后再 `CDbl` / `CCur` / `CDate` / `CBool`）。`vouch` 上的 `iexchrate`、`cexchname` 都要写。不要写 `guid`（空串进 uuid 字段报错）。
- 布尔：根上 `bsamehx="True"`，`bprepay` 写 `0` / `1`（收付款单行的 `bPrePay`），`bcancelall="false"`。金额不带千分位。日期 `yyyy-MM-dd`，不带时间。
- 外币：`close` 和每个 `vouch` 的 `cexchname`、`iexchrate` 都写收付款单的；`*_f` 为原币，`ijsdhxamount` / `icancelamount` 为本币 = 原币 × 收付款单汇率四舍五入到分，`close` 按合计折算、各 `vouch` 逐个折算、尾差并到第一个 `vouch`。单据自身汇率与收付款单不同产生的差额由期末汇兑损益 `9M`（`SYRAR…`）另记，不在 `Save` 里。一个核销号内不混用汇率。
- 写入：`Ar_Detail` / `Ap_Detail` 插 `cProcStyle='9P'` 行，带同一新 `cCancelNo`（`HXAR` / `HXAP` 加 13 位数字）：每个被核销单据行一条（`cVouchType` / `cVouchID` 为收付款单，`cCoVouchType` / `cCoVouchID` 为被核销单据，`iBVid` 为该行，应收记贷方、应付记借方），另有一条收付款单自身的冲减行（`iBVid=0`，金额为负）；`Ap_CloseBills.iRAmt_f` / `iRAmt` / `iRAmt_s` 减核销额；`Ap_CloseBill.cCancelMan` 为空时写入；回写销售发票 `SaleBillVouchs.iExchSum`（`iMoneySum`）、采购发票 `PurBillVouchs.iOriTotal`（`iTotal`）、应收应付单 `Ap_Vouch.iRAmount*`。不生成凭证。
- 预收（`bPrePay=1`）行核销时 `Save` 可能另插 `Ap_CloseBills` 行（`iType=1`），余额核对需计入这些新行。
- 事务里的核对抛错时先看 `@@TRANCOUNT`：已为 0 说明数据库已回滚（如死锁牺牲品），未写入任何数据，返回 503。

### 自动核销（不用 `cLsCancel.AutoCancel`）

- `cLsCancel.AutoCancel(m_GECondition, m_JSDCondition, m_DJCondition, sXml, bReport, ByRef xmlReport, ByRef errMsg)` 在事务中调用时返回 true、`xmlReport` / `errMsg` 为空串，但不写任何 9P 行、余额不变（它依赖 U8 定时核销的上下文），不要用。U8 界面的自动核销同样不调它，而是自行配对后走同一个 `Save`。
- 桥同样自行按 U8 规则配对，每行收付款单拼一份 `canceldata`（一个 `close`、若干 `vouch`），逐批 `Save`，全部在一个事务里。
- U8 的配对（`iHxRule=0`、`bAPAutoCancelWithHxRule=0`）：同一往来单位下有余额的结算单（`cCoVouchType` 48 / 49）对有余额的发票、应收应付单；不按单据号、订单、合同、存货配对。选项打开时 U8 另按 `cHxRule`（订单号、合同号、存货等）配对，桥不实现，409。桥的排序为单据日期、主键 / 单号、行。
- `Save` 不查日期、期间、锁（见「核销」），每一批由桥在事务里检查。
- 预收 / 预付行（`bprepay="1"`）缺省不纳入，请求 `include_prepay: true` 时纳入。外币只在同币种之间配对，不比较单据汇率。
- 桥每次最多 200 行单据，分到最多 20 批。

### 取消核销

- 没有可调用的组件。`cLsCancel.ACCJZ_Cancel(cType, cID, bKeepClue)` 传 `HX` / `AR` / `9P` 都返回 true 但不执行任何 SQL。U8 界面「其他处理 → 取消操作」直接执行 SQL，不开事务；列表条件 `cProcStyle=N'9P' and cVouchType<>cCoVouchType and cVouchType<>N'9O' and cpzid is null and cflag=N'AR'`。桥按同样的 SQL 在一个事务里执行（与 U8 界面执行的 SQL 一致，实测核对）。
- 取消前检查：`GL_mend` 上核销行所在期间 `bFlag_AR` / `bFlag_AP = 1` 时拒绝；存储过程 `AR_ExistUnAuditCloseBill`（无参数，全账套：`Ap_CloseBill.cFlag='AR'`、未审核、`iType<2`、无核销号）返回 1 时拒绝，应付侧无对应过程；`cPZid` 非空的核销需先删凭证。U8 用 `LockVouch` 给收付款单和发票加锁；桥不写、不查 `LockVouch`，靠事务内的 `UPDLOCK, HOLDLOCK` 和同侧串行。
- 本批之后同一单据上（作为对方单据或本单据，`Auto_ID` 更大、核销号不同）有审核登记（Sign）以外的任何处理时，不能取消前面的批次。桥不列举处理方式（实际会出现 `9P`、`9N` 红票对冲 `HRAR…` / `HPAP…`、`9M`、`9E`、`9I`、`9J`、`9A`、`9C`、`9D`、`BZ` 等），而是排除 Sign 行：`cProcStyle` 为 `26`、`27`、`R0`、`48`、`49`、`01`、`02`、`P0`，或等于该行的 `cVouchType`（如期初 `50`）。
- 按核销号读整批（取消时带 `UPDLOCK, HOLDLOCK`）一律带 `cProcStyle=N'9P' AND cCancelNo=? AND cFlag=?`：往来明细上含 `cCancelNo` 的索引只有 `INDEX_Ar_Detail_HXZD` / `INDEX_Ap_Detail_HXZD`（`cProcStyle, cCancelNo, cFlag`），只带 `cCancelNo` 会整索引扫描，在事务里还会把范围锁挂满整个索引、挡住所有往来明细插入。`HX` 前缀的核销号只出现在 `9P` 行上。
- 顺序（一个事务）：带锁快照整批 9P 行 → 收付款单行 `Ap_CloseBills.iRAmt_f/iRAmt/iRAmt_s += -(借+贷)`（只取冲减行：`cCoVouchType like '4%'` 且 `iCoClosesID<>0`；单据侧的 9P 行带同一 `iClosesID`、金额为正，混入会导致方向相反）→ `Ap_Vouch`（`R0` / `P0`）`iRAmount_f/iRAmount += 借+贷`，`iRAmount_s` 按 `iAmount_s * iRAmount_f / iAmount_f` 重算 → 销售发票：建 `#ap_SaleBillVouchHXdata(autoid, iexchsum, imoneysum)`，从 9P 行填 `-(借+贷)`（`bReturnFlag=1` 取反，`csign='F'` 按 U8 的公式），再调 `Ussaupdispatch.clsWrite2Bill.UpdateBillForAR(ByRef 连接, ByRef "#ap_SaleBillVouchHXdata") As String`（不调 `Init`；by-ref `{0,1}`；返回空串为成功，非空为错误原文；使用传入的连接，临时表和事务都是桥的）。它除 `SaleBillVouchs.iExchSum/iMoneySum` 外还回写 `so_sodetails`、`dispatchlists` 的累计核销和 `sa_creditsum.fblsum`，不要只改发票 → 采购发票：经 `#ap_PurBillVouchHXdata` → `#billtmp` 回写 `PurBillVouchs.iOriTotal/iTotal`，不调 `UpdateBillForAP` → 收付款单各行 `SUM(iRAmt_f-iAmt_f)` 均为 0 时 `Ap_CloseBill.cCancelMan=NULL, bPrepay=0` → `DELETE ... WHERE cProcStyle=N'9P' AND cCancelNo=… AND (cflag=… OR cbustype=N'代理进口')`。
- 临时表要在不带参数的批里建（`Connection.Execute`；带参数的语句走 `sp_executesql`，其中建的 `#` 表出了那层即消失），批内用 `SET NOCOUNT ON` / `OFF` 包住，避免行数消息把后续语句卡在结果集里。
- 「保留线索」（把 9P 行取反、以 `Ap_Proc_CancelNo @cType=N'HX'` 的新号另插一批）只在 `AccInformation` 的 `bAR2Cancel` / `bAP2Cancel` 打开时发生，桥不实现。`bCancelKeepAddAll` 是存货核算的选项，与此无关。
- `Ap_CloseBills` 上的触发器 `TR_Ap_CloseBills` 在不同账套可能启用或停用，不能假定一致。
- 功能权限：取消核销、取消处理为 U8「取消操作」`AR0807` / `AP0807`。

### 制单、取消制单（桥拼分录 + `U8PzInsert`）

- 没有无界面的制单组件：U8 界面的凭证由总账组件写入；`MakeVouchers` 签名不明；`U8ApCancel.cLsCancel.SaveAccVouch*` 只做核销制单；`UFAPBO.ClsAccVouch.CreateAccVouchByPush` 是暂估应收。都不要用。
- 桥的做法：按 U8 规则拼分录，经总账新增所用的导入组件 `U8PzInsert.clsPZInsert.Transact` 保存（`ToEAICon` 设为请求连接，`Transact(xml, login)` by-ref `{1}`，自行提交，不能包 `CoTrans`），再在桥的事务里执行与 U8 相同的回写。一张单据一张凭证，或同类型多张单据合并为一张（见下文「合并制单」）。以下分录规则已与 U8 客户端生成的凭证逐张核对。
- 往来科目取原始往来明细的 `cCode`（来自控制科目设置 `AP_CtrlCodeSet`，见「发票的应付审核、应收审核」）。销售发票收入 / 销项税、采购发票采购 / 进项税、应收应付单税金：先查对方科目设置 `AP_OppCodeSet`，无匹配行时用基本科目设置 `Ap_InputCode`（`cNote_f`：`xssrkm` 销售收入、`xssjkm` 应交增值税、`cgkm` 采购、`cgsjkm` 采购税金、`kzkm` 控制、`prekm` 预收付、`pjkm` 票据；`cFlag` R / P，`cArCode` / `cApCode`，`iyear`）。税额按基本科目，不按税率区分科目。存货分类按前缀匹配、组合键的优先级是桥定的规则，未与 U8 客户端逐项对照。
- 金额：销售发票往来 = 明细 `iDAmount`（= 表体 `iNatSum`），收入 = 表体 `iNatMoney`，税 = `iNatTax`；采购发票 `iMoney` / `iTaxPrice` / 明细 `iCAmount`；收付款单全部取往来明细：`iFlag=6` 结算行（`cCode` 为现金、银行或票据科目，带 `cSSCode`），`iFlag=0` 往来行，`iFlag=3` 票据登记行（与结算行同科目同金额，不出分录，只回写 `cPZid`）；应收应付单对方 = 表体 `Ap_Vouchs.cCode`、`iNoTaxAmount`，税 = `iNatTax`。明细 `iBVid` 即发票表体主键。
- 同科目同辅助项合并（`AccInformation` 应收 / 应付的 `bYPzKMHB`、`bSPzKMHB`）：多行应收明细合成一行借方；收入按科目 + 项目（存货）合并。借方在前。摘要为往来明细的 `cDigest`。凭证类别：银行收款「收」、付款「付」，票据收款和发票、应收应付单「转」（`dsign` 只有收付转，不用总账的 `cSignDefaultCw`）。`doutbilldate` 等于 `dbill_date`。
- 辅助项只按科目要求填：`1122*` 客户、`2202*` 供应商、`6001*` 项目（大类 `ch` 存货核算，`fitem.ctable='inventory'`，项目编码即存货编码），`1401` / `2221*` 无；部门、个人不从发票抄到往来行。有客户 / 供应商的行 `cname` 为业务员姓名，无业务员时写 `-`。现金流量挂在对方行上，项目取自 `GL_CashItemDataSource`（`cDataSource` 科目前缀，`bDir` 1 借方、0 贷方，注意起止日期）：收款 `1122` 贷方 → `01`，付款 `2202` 借方 → `04`。
- 来源列：`coutsysname` = AR / AP，`coutsign`（发票、应收单同 flag；付款单 `RP`；收款单桥写 AR；应付单写 `AR`），`coutno_id` = 外部业务号，`coutbillsign` = 单据类型，`coutid` = 单号，`coutaccset` = 账套号，`ioutyear` / `ioutperiod` = 制单日期的年月，`bvouchAddordele=1`，受控科目（`code.cother` 非空）行 `bvalueedit=0`、其余 1，`idoc` ≥ 1。EAI 报文对应：`voucher_making_system` → `coutsysname`（填 AR 可通过；被拒时改 GL 重导）、`reserve1` → `coutsign`、`reserve2` → `coutno_id`、`bill_type` / `bill_id` / `bill_date` → `coutbillsign` / `coutid` / `doutbilldate`、辅助项 `operator` → `cname`。桥在回写事务里再按上述值 `UPDATE` 一遍（只改未记账的这一张）。
- 外部业务号不是 `GL_accvouch` 的最大号，而是 `Ap_CancelNo`（`cType='PZ'`，`cFlag` AR / AP / GL / IA 各一行）加一；格式为 AR / AP 加 13 位补零。U8 客户端取号是先读后 `update … where iCancelNo<n`，读时不等待其他会话的 `UPDLOCK`，两边可能取到同一个号；桥带锁取号（已占用则往后跳），提交前再核对，撞号则回滚、删除本凭证、409。
- 防重复制单：`Transact` 自行提交，之后任何一步出错都返回 504，此时凭证可能已在总账而往来明细还没有 `cPZid`。因此制单前另查总账：已有 `coutbillsign` = 单据类型、`coutid` = 单号、`coutsysname` 为本系统或 `GL` 的凭证，且其 `coutno_id` 未被本单任何往来明细引用，即 409 并点名该凭证（存货核算的凭证可能也带发票的 `coutbillsign` / `coutid`，按制单系统排除）。
- 退款单（供应商退款 `AP`/`48`、客户退款 `AR`/`49`）：审核时写的往来明细为负数。U8 生成的退款凭证：供应商退款两行都在借方，往来科目（带供应商）借 −金额、银行科目借 +金额；客户退款两行都在贷方，往来科目（带客户）贷 −金额、银行科目贷 +金额；往来行在前，借贷合计可以为 0。桥照此生成红字分录；凭证类别供应商退款「收」、客户退款「付」；`coutsign` 为 `RP`；摘要缺省「收<供应商>退款」/「付<客户>退款」。现金流量挂在非现金行上，沿用该行的列和符号，项目方向按分录所在列取（含负数）。往来明细只认 `iFlag` 0 / 3 / 6，其他行或借贷不平 409。
- 现结 / 现付发票（往来明细有 `cProcStyle='XJ'` 行）在 U8 中走「现结制单」，分录需带现结的结算科目；桥不实现，409「现结发票请在 U8 客户端制单」。
- 分录上的档案值按总账新增的规则再查一遍（部门末级、人员、客户、供应商、结算方式存在，现金流量项目未关闭）：`GL_CashItemDataSource` 可能指向已关闭的项目，不拦截则导入器要么笼统拒绝、要么照写。
- 回写：往来明细 `cPZid`、`dPZDate`、`cGLSign`、`iGLno_id`，`ino_id` 是该明细行所在分录的分录号（按行不按科目：辅助项不同或不合并时，同科目的几行明细各自是 1、2、3；合并成一行时都指向那一行），往来行、票据结算行有值，票据登记行（`iFlag=3`）跟同科目的结算行，银行 / 现金结算行为 NULL；`csign`、`isignseq` 是审核时写的，不动。发票表体 `cClue` = 外部业务号（覆盖应付审核时写的线索号）、`cPZNum` = 类别 + `-` + 四位补零凭证号、`dSignDate`，销售 `cIncomeSub` / 采购 `cDebitHead` = 往来科目；`Ap_CloseBill` / `Ap_Vouch` 表头 `cPZID`、`cPZNum`、`doutbilldate`。
- 合并制单（`ids`）：没有单独的组件。U8 合并凭证有三种形态：（A）多张单据一个 `coutno_id`，每行 `coutid` 为该行来源单据的单号，往来行不跨单据合并，`idoc` = 单据张数，往来明细各自的 `ino_id` 指向自己那一行；（B）`coutid` 带「 序号」后缀；（C）多张发票一个 `cPZid`、`coutid` 只写其中一张。桥按（A）：每张单据各自拼分录、只在本单内合并，回写事务里按行写各自的 `coutbillsign` / `coutid`。合并选项 `bUniteDigest` / `bUniteSettle` / `bUniteNote` / `iUniteDefine` 不支持。核销制单（`9P`，`bHXNeedZd`）不支持。取消制单按外部业务号清，合并凭证一次清掉全部单据。
- 取消制单（U8 删除凭证后的回写）：`SaleBillVouchs` / `PurBillVouchs` 按 `cClue` 清三列；`Ar_BadPara`、`AR_RZDetail`（连同 `cGLSign`、`iGLno_id`）、`Ap_Vouch`、`Ap_CloseBill`（连同 `cPreCode`）、`Ap_Note_Sub`、`CM_Balance` 按 `cPZID` 清；往来明细清 `cPZid`、`cGLSign`、`iGLno_id`。桥只取消单据原始行（`cProcStyle = cVouchType`、本方 `cFlag`）生成的凭证；`9E`、`9I`、`9J`、`9M`、`9N`、`9A` / `9C` / `9D`、`BZ`、`9P` 等处理生成的凭证也带原单的 `coutbillsign`，U8 删除时各有专门回写，桥不实现：两张往来明细中只要有一行引用该凭证却不是原始行即 409。凭证本身按总账删除的规则删 `GL_CashTable`、`GL_CodeRemark`、`GL_accvouch`。U8 删除凭证的拒绝原文：「此凭证已记账，不能删除」「此凭证已审核，不能删除」「此凭证已经出纳签字，不能删除」「此凭证所在期间已结账，不能删除」。弃审前需先取消制单（`CancelSign` 拒绝有 `cPZid` 的单据）。
- 未验证：以 AP 子系统登录、`voucher_making_system` 填 AP；受控科目在导入时是否受总账选项 `bUseYSKM` / `bUseYFKM` 限制；收款单的 `coutsign` 应为 AR 还是 RP。
- 功能权限：`AR0508` / `AP0508`（生成凭证）。

### 坏账处理

坏账计提、发生、收回及其取消、制单属第二级写入（复现 U8 界面的 SQL，与 U8 界面执行的 SQL 一致，实测核对）：缺省关闭，`enableReplicatedWrites` 打开后也只对 `testAccounts` 中的账套开放，见 `configuration.md`。正式账套请在 U8 客户端操作。

- 功能权限：计提 `AR050601`，坏账发生 `AR050602`，坏账收回 `AR050603`；取消走「取消操作」，制单走「制单处理」。没有可调用的组件：发生、收回的往来明细由 U8 用记录集 `AddNew` 写入（同转账 9I），参数行由 SQL 更新。处理方式名称在 `AP_VouchType_base`：9F 计提坏账、9G 坏账发生、9H 坏账收回。
- 处理号：三种都用 `Ap_CancelNo` 的 `cType=N'HZ'`、`cFlag=N'AR'`，即 `HZAR` + 13 位补零（如 `HZAR0000000000001`），无单独计数行；区分种类看往来明细的 `cProcStyle` 或参数行的 `cCancelNo`。
- 坏账准备参数 `Ar_BadPara`：每年一行（`iYear`、`iJtStyle` 计提方法、`nJtRate` 比率、`nQcYe`、`cHzCode` 坏账准备科目、`cDyCode` 对方科目、`dJtDate`、`iJtAmount`、`iRemainAmount`、`cProcStyle`、`cCancelNo`、`cPZID`）。科目只在这一行上，不在基本科目设置里。没有参数行时 `AccInformation` 应收的 `iBadAccDeal` 仍可能有值，U8 提示未设置坏账参数。账龄区间在 `Ar_BadAge`，`AccInformation` 的 `BadAgeAnalyseDate=1` 时按收款条件的信用天数推后。
- 坏账发生（9G）：可选单据 26 / 27 / 28 / 29 和 `Ap_VouchType like N'R%' and <> N'RZ'`（含期初 R0），不含收付款单；只选正余额，金额大于 0 且不超过余额。每张单据（行）一行 `cProcStyle=N'9G'` 的贷方明细（`iCAmount` / `iCAmount_f`，`cVouchType = cCoVouchType` 为原单据，`cCode` 为原单据的应收科目，`iBVid` 为发票行，`dRegDate = dPZDate`，`cPZid` 空），余额回写同转账：应收单 `Ap_Vouch.iRAmount*`，发票 `#ap_SaleBillVouchHXdata` + `UpdateBillForAR`。参数：`update Ar_BadPara set iRemainAmount=iRemainAmount-(金额) where autoid=(select max(autoid) from Ar_BadPara)`，不按年度；没有参数行时 U8 改 0 行仍保存，桥 409。
- 坏账收回（9H）：选该客户、该币种、未审核（`cCheckMan is null`）、未核销（`cCancelNo is null`）、`iType=0` 的收款单（48），不另建应收单。一行 `cProcStyle=N'9H'` 的借方明细挂在收款单上（`cVouchType = cCoVouchType = N'48'`，`iCoClosesID` 为收款单行），收款单表体 `iRAmt*` 清零；参数 `iRemainAmount += 金额`（同样按 `max(autoid)`）。取消时整张收款单复原（`iRAmt=iAmt`），因此收回金额必须等于收款单全部余额。
- 坏账收回经审核组件：U8 的 9H 只写借方处理行、用 SQL 填审核人，不写收款单自身的审核行（贷方），客户往来余额 Σ(`iDAmount`−`iCAmount`) 会多出收回金额。正确的账是「借 应收 / 贷 坏账准备」加「借 银行 / 贷 应收」，应收净额不变、坏账准备加回。所以桥在同一事务里先用 `UFAPBO.clsCloseBill.Sign` 审核收款单（组件写审核行 `cProcStyle=N'48'`、`cCancelNo=N'AR48'+单号`、`cCheckMan`、`dverifydate`），再写 9H 行（`cCode` 取收款单行的应收科目 `Ap_CloseBills.cKm`；表头 `cCode` 是结算科目，只在制单时作借方银行科目）、清零 `iRAmt*`、加参数余额，提交前核对该收款单的往来明细借贷相抵；取消时反向，最后 `CancelSign`。没有审核行的 9H（U8 客户端所做）桥不取消。9H 凭证（借 银行 / 贷 坏账准备）同时是该收款单的收款凭证，制单时一并回写收款单的审核行和表头 `cPzID` / `cPZNum` / `doutbilldate`，取消制单一并清掉。
- 计提（9F）：先查 `GL_mend.bFlag_AR`、`select top 1 * from ar_badpara where iYear=年度`。基数：1 应收余额 `sum(iDAmount)-sum(iCAmount)`（`cFlag=N'AR' and iFlag<3`、无业务类型）；2 按 `Ar_BadAge` 各区间（`dRegDate + isnull(iPayCreDays,0)` 落在区间内）的余额 × 区间比率；3 `Sum(SaleBillVouchs.iNatMoney)`（已审核、非期初、未作废）× 比率，取数区间为 `date` 所在年度 1 月 1 日到 `date`。本次计提 = 应计 − 当前余额（增量）。写入：`update ar_badpara set djtdate=…, iRemainAmount=isnull(iRemainAmount,0)+本次, iJtAmount=isnull(iJtAmount,0)+本次, cprocstyle=N'9F', cCancelNo=N'HZAR…' where iyear=年度`；该年度没有行时 U8 插一行，桥不插、409。不写往来明细。同一年度再次计提仍按增量更新这一行，参数行只记最后一次的处理号，`iJtAmount` 为累计数。直接销售法（`iJtStyle=4`）不支持，计提 409。
- 取消：列表 `cProcStyle in (N'9F',N'9G',N'9H') and iperiod>0 and cPzID is null`，9F 另从参数行取（`dJtDate<>''` 且 `cPZid` 空）。共同条件：未制单、`GL_mend.bFlag_AR` 未结账、同一单据之后没有审核登记以外的处理。9F：该年度参数行多于一行时删掉 `cCancelNo` 对应行，否则 `iRemainAmount=iRemainAmount-iJtAmount, iJtAmount=null, dJtDate=null`。9H：参数 `iRemainAmount -= 借方金额`，收款单 `iRAmt=iAmt`（三组）、`cCheckMan` / `dverifysystime` / `dverifydate` 清空，删 9H 行。9G：参数 `iRemainAmount` 加回，余额反向回写（同取消转账），删 9G 行。
- 制单：9F 取参数行（`dJtDate` 非空、`cPZid` 空），9G / 9H 取往来明细；凭证来源 `coutsign` 为 `JT`，取号族 `HZ`。分录：9F 借 `cDyCode` 贷 `cHzCode`；9G 借 `cHzCode` 贷原单据应收科目；9H 反向，借方科目取收款单的结算科目。回写：9F `Ar_BadPara.cPZID`（取消制单 `Update Ar_BadPara Set cPZID=null Where cPZID=…`），9G / 9H 往来明细的 `cPZid` / `cGLSign` / `iGLno_id`。
- 未与 U8 客户端对照：计提方法 3 的取数区间、取消坏账收回时参数余额的修改次数、9H 制单的借方银行科目、9F 制单是否写 `iFlag=3` 行。

## 6. 销售

登录 `SA`。`USSAServer.clsSystem`：`Init(login)`、`INIMySAInfor()`；然后 `VoucherCO_Sa.ClsVoucherCO_SA.Init(vt, login, conn, "CS", clsSystem)`，之后才设 `bManualTrans = true`。

| 单据 | VT | 卡片 | 数据来源 |
| --- | --- | --- | --- |
| 销售订单 | 12 | `17` | `SaleOrderQ`（`saleorderq.id=?`） |
| 蓝字发货单 | 9 | `01` | `Sales_FHD_T`（`sales_fhd_t.dlid=?`） |
| 退货单（红字发货单） | 10 | `03` | 同蓝字发货单（卡片 03 与 01 的扩展列相同） |
| 销售专用发票（`cVouchType` 26） | 0 | `07` | `SaleBillVouchZT` |
| 销售普通发票（27） | 2 | `13` | 同上 |
| 退货申请单 | 34 | `SA31` | `SA_ReturnsApplyMain` / `SA_ReturnsApplyDetail`（`GetVoucherData` 可读，桥直接查表） |

扩展列：`select cextendfield, cextendjoin from voucherextendinfo where cardnumber=? and cextendtype='T' and bextend='2'`，SQL 前加 `'' as editprop`。

| 操作 | 调用 | 要点 |
| --- | --- | --- |
| 读取 | `GetVoucherData(头, 体, id, true, "", (short)0)` | 第四个参数让 U8 按操作员数据权限过滤 |
| 审核、弃审 | `VerifyVouch(dom, bool)` | 空串为成功。「已审」看审核人 `cVerifier` 和审核日期 `dverifydate` 均非空；`iverifystate` 不能作审核标志 |
| 订单新增 | `GetDefaultVoucherDom` 取模板，`GetVoucherNO` 取号，`Save(头, 体, 0, vNewID)` | 表体要有预完工日期 `dPreMoDate`；组件不补预发货日期 `dPreDate`（不带则存空），桥按表头预发货、单据日期补；多计量单位存货要补计量单位组、单位、换算率、件数 |
| 订单修改 | `GetVoucherData` 读入，按 `editprop` 标行，`Save(头, 体, (short)1, vNewID)` | U8 不写修改人、修改日期，需自行写 |
| 订单关闭、打开 | `OrderClose(头, bClose)`（整单）或 `OrderClose(头, bClose, iSOsID)`（一行；多行逐行调，每次重新装表头） | U8 对未审核订单也会关闭，调用前自行拒绝。整单写关闭人和全部行的关闭人 |
| 发货单 ← 销售订单 | 来源视图 `sale_RefSOVouch_T`（`id=?`）、`sale_RefSOVouch_B`（`isosid=?`），只拷目标 schema 中有的属性，跳过主键、单号、`ufts`、制单审核关闭人、累计数量等列。表头 `cvouchtype=05`、`breturnflag=0`、`editprop=A`，`GetVoucherNO` 取号后 `Save(…, 0)` | CO 自行增加订单行累计发货数 `iFHQuantity` |
| 发货单删除 | `Delete(头, 体)`，删除前把每行 `editprop` 标为 `D` | 累计发货数回退 |
| 发货单修改 | VT 9 的 `GetVoucherData` 读入，表头、改的行 `editprop=M`，`Save(头, 体, (short)1, "")` by-ref `{3}` | 返回 null 为成功，留在请求连接的事务里；U8 按数量差调整订单行 `iFHQuantity`，但不重算金额、不写修改人，由桥处理 |
| 退货单 ← 蓝字发货单 | VT 10 的 `GetNegaVouchData("05", 头, 体, 蓝字 DLID, err, true)`，by-ref `{0,1,2,3,4,5}`，返回 U8 生成的整张红字 DOM（`breturnflag=1`、`bneedbill=1`，`ivtid` 为蓝字模板，表体为原行全数取负，`autoid` / `autoid2` / `dlid` 为原单主键，不挂订单）。可保存的 DOM：表头 `editprop=A`、`dlid=""`、去掉 `ufts`、`ivtid` 按卡片 03 重取、`cdlcode` 取号、`csocode` = 订单号、`bneedbill` 0 或 1；表体只留要退的行，`editprop=A`、`autoid` / `dlid` 空，`iquantity` / `inum` 为负的退货数，`funsignquantity` / `funsignnum` 为正，`iretquantity` / `fretqtywkp` / `fretqtyykp` 为 0，`icorid` = 原 `iDLsID`、`ccorcode` = 原单号，`isosid` = 原 `iSOsID`、`csocode` = `cordercode` = 订单号、`iorderrowno` = 订单行号。`GetVoucherNO` + `Save(…, 0)` | 保存后 U8 对原发货行检查（在回写之后计算）：`CASE WHEN ABS(iSettleQuantity) - (ABS(iQuantity) - ABS(fretqtywkp)) > 0 THEN ABS(iQuantity)-ABS(fretqtywkp) ELSE ABS(iSettleQuantity) END - ABS(fretqtyykp) < 0` 时报「退货数量不能大于应发货数量」。表头 `bneedbill=1` 按已开票退货，退货数加到原行 `fretqtyykp`；`bneedbill=0` 按未开票退货，加到 `fretqtywkp`。U8 还回写原行 `iRetQuantity` 加、订单行 `iFHQuantity` 减、`fretquantity` 加，都在本事务里，桥提交前核对 |
| 退货单审核、删除 | 同发货单，VT 10；删除前每行 `editprop=D` | |
| 销售发票 ← 发货单 | 来源视图 `Sales_FHD_T`、`Sales_FHD_W`（`idlsid=?`）。表头必须带 `sbvid=""`、`idisp=1`，表体 `cbdlcode` = 发货单号 `DispatchList.cDLCode` | CO 写发货行累计开票数 `iSettleQuantity`。来源视图无 `idisp`，不写则为列缺省 0，U8 视为先开票，之后修改报「先开票不可以参照发货单」 |
| 红字销售发票 ← 退货单 | VT 1（26）/ 3（27）。先试 `GetNegaVouchData("26" 或 "27", 头, 体, 退货单 DLID, err, true)`，by-ref `{0,1,2,3,4,5}`；返回 false、没有表头或表体 `idlsid` 不含全部请求行时，改用同蓝字的 `GetDefaultVoucherDom`（卡片 07 / 13）加参照视图 `Sales_FHD_T`、`Sales_FHD_W`（两视图都含退货单，数量为负）。表头 `sbvid=""`、`cvouchtype`、`idisp=1`、`breturnflag=1`、`cdlcode` = 退货单号；表体 `idlsid` = 退货行、`cbdlcode` = 退货单号、`iquantity` 为负。`GetVoucherNO` + `Save(…, 0)` | 红票 `iVTid` 同蓝字。退货行 `iSettleQuantity` 等于发票数量（负）；原蓝字发货行 `fretqtyykp` 在退货单保存时已写，红票不再加；订单行 `iKPQuantity` 为净数（蓝票 − \|红票\|），在保存时回写，复核、弃复不再变 |
| 红字销售发票 ← 蓝字发票（红冲） | `Init(1 或 3, …)`（26 → VT 1，27 → VT 3），`GetNegaVouchData("26" 或 "27", 头, 体, 蓝字 SBVID, err, true)`，by-ref `{0,1,2,3,4,5}`。传**蓝字发票的 `SBVID`** 返回 true 和整张红字发票 DOM：表头 `sbvid=0`、`csbvcode` 空、`breturnflag=1`、`cvouchtype` 和客户同蓝字；表体为蓝字行取负，`autoid` 是**蓝字行的主键**（需清掉），`idlsid` 为空。传对应发货单 `DLID` 返回 false。桥按请求保留行、改数量，`GetVoucherNO` + `Save(…, 0)` | U8 无可用的红蓝发票关联列（`iSBVID` 通常为 0）：桥在红字行 `SaleBillVouchs.iSBVID` 写蓝字行 `AutoID` 作关联，剩余数量按它算，保存后核对；蓝字发票被红冲后桥拒绝弃复、删除、修改。发货行 `iSettleQuantity`、`fVeriBillQty`，订单行 `iKPQuantity` 只记审计；U8 是否另生成发货单未验证 |
| 退货申请单新增、修改、删除、审核、弃审 | `Init(34, login, conn, "CS", sys)`；读入 `GetVoucherData(头, 体, ID, true, "", 0)`（表体 `iquantity` 为负，带 `idlsid` / `cdlcode`）；新增取空白模板挂发货行（`idlsid`、`icorid`、`cdlcode`），`GetVoucherNO` + `Save(头, 体, 0, "")`；修改 `Save(…, 1, "")`；删除每行 `editprop=D` 后 `Delete(头, 体)`；审核 `VerifyVouch(头, bool)` | VT 34 的 `Save`、`Delete`、`VerifyVouch` 转给 .NET 的 `SaVoucherService.clsSaVoucherService`，它在传入连接上自行 `BeginTrans` / `Commit`，**不看 `bManualTrans`**；外层包事务时 `Save` 报「SaveVoucher:无法在此会话中启动更多的事务。」。所以该卡片的写入不包事务，按自行提交的组件处理（检查在调用前做完，之后核对失败一律 504）。数量在卡片上为负 |
| 退货单 ← 退货申请单 | 同退货单 ← 蓝字发货单（VT 10，`GetNegaVouchData("05", …, 申请行所挂的蓝字 DLID, …)`），只保留申请行对应的发货行，表体另写 `irtnappid` = 申请行 `AutoID`、`crtnappcode` = 申请单号，表头 `bneedbill` 取申请行的 | U8 保存时回写申请行已退数量 `fretqty`（负数，更负），在本事务里，桥提交前核对；删除退货单时退回。参照申请单生成的退货行改数量、删行时 U8 如何处理 `fretqty` 未验证，桥拒绝 |
| 销售发票复核、弃复 | 表头 SQL `SaleBillVouchZT … where sbvid=?`，`VerifyVouch(头, bool)` | 写复核人 `cChecker`；`cVerifier` 属应收审核（见「发票的应付审核、应收审核」）。应收已审的发票弃复时 U8 返回「单据已经在应收系统审核」，需先应收弃审 |
| 销售发票删除 | `Delete` 前把每行 `editprop` 标为 `D` | 不标 `D` 也能删，但 `iSettleQuantity` 不回退 |
| 无来源发货单 | VT 9 的 `GetDefaultVoucherDom(conn, "01", 头, 体)` 取模板，调用方字段写进种子行，单行 `BodyCheck` 算价税，`getDefaltVTID` + 制单人，`GetVoucherNO` + `Save(头, 体, 0, vNewID)` by-ref `{3}` | 表头 `cbustype=普通销售`、`cvouchtype=05`、`breturnflag=0`，表体不写 `isosid`。销售选项 `SA.bMustSO_ptxs`（普通销售必有订单）为 TRUE 时桥 409 |
| 先开票销售发票 | 同上，VT 0 / 卡片 07（26）或 VT 2 / 卡片 13（27）；表头 `idisp=0`、`sbvid=""`，表体不写 `idlsid` | U8 在发票保存时生成发货单（`DispatchList.SBVID` 指回发票），无需复核；删除发票时一并删除该发货单。选项同无来源发货单 |
| 销售发票修改 | 同发货单修改，VT 按 `cVouchType`（26 → 0，27 → 2） | 参照发货单的发票若 `iDisp=0`，保存时 U8 报「单据[..]第1行 存货[..]的记录不正确，先开票不可以参照发货单！」；修改时写 `idisp=1` 并补表体 `cbdlcode` |

销售出库（`USERPCO.VoucherCO`，登录 `ST`）：`MakeOutVouch(DLID, new DispatchWrapper(null), errMsg, conn, domMsg, true)` 返回布尔，一次生成该发货单剩余的全部行，不能指定部分数量；跨仓库时一张发货单生成多张出库单。`vouchers/generate` 的 `sale_out` 带 `lines` 时桥按行部分生成：先整单生成并提交，再在另一事务里改小、删掉未请求的行，失败则删除生成的单据补偿（见「销售出库按行部分出库」）；不带 `lines` 时同样把超出剩余数量的部分改回。新单据按发货单号 `cDLCode` 在 `rdrecord32` 上找：调用前记下 `max(ID)`，调用后取更大的 `ID`，没有则说明 U8 未生成。

无来源销售出库（仅当账套未启用销售管理时）走 `USERPCO.VoucherCO` 的 `Insert("32", …)`，同其他出库单：表头 `cvouchtype=32`、`cbustype=普通销售`、`csource=库存`、`brdflag=0`、`vt_id=87`，表体不写 `iDLsID`。账套已启用销售管理（`AccInformation` 的 `SA` / `dSaleStartDate` 非空）或库存选项 `ST.bSAcreat`（销售出库单由销售系统生成）打开时桥拒绝。修改销售出库单时桥一律不允许换仓库（不依赖 `ST.bCanModifySaleoutWh`）。

## 7. 采购

登录 `PU`。先初始化 `Info_PU.ClsS_Infor`，再 `VoucherCO_PU.Init`。

| 操作 | 调用 | 要点 |
| --- | --- | --- |
| 订单、到货单读取、审核、弃审 | `GetVoucherDataById`，`ConfirmPO` / `CancelconfirmPO` / `ConfirmArr` / `CancelconfirmArr` | 空串为成功 |
| 订单新增 | 空白 DOM：`zpurpoheader`、`zPurpotail` 视图 `where 1=2` 各加一行。`GetVoucherNO(头, "88", err, no)` 取号，`VoucherSave2(头, 体, (short)2, "")` | 第 4 个参数返回新主键 |
| 订单修改 | `GetVoucherDataById` 读入，`editprop` 同销售，`VoucherSave2(头, 体, (short)1, "<POID>")` | 改过和新增的行重算金额 |
| 订单关闭、打开 | `ClosePOItems(头, 体, errMsg, 整单?)` / `OpenPOItems` | 返回处理的行数（short），`errMsg` 空且大于 0 才算成功。按行时先从 DOM 删掉其他行，第 4 个参数 false。全部行关闭后才写表头关闭人 |
| 到货单删除 | `Init` 的 VT 为 2，读入后 `Delete` | U8 自行回退订单累计到货 |
| 到货单关闭、打开 | `Init` 的 VT 为 2，`GetVoucherDataById` 读入，`CloseArrItems(头, 体, strErr, 整单?)` / `OpenArrItems`，by-ref `{2}` | 签名同 `ClosePOItems`，但第 4 个参数按值。无专用存储过程。桥不看返回值，`strErr` 非空作 409；提交前在同一事务里核对行关闭人 `cbcloser` 到达目标状态，按行时另核对未选行未被改动。表头写 `ccloser`、`dclosedate`（无关闭时间列），行写 `cbcloser`、`dlineclosedate`；关闭人为操作员姓名。按行关闭后表头何时写关闭人未验证 |
| 到货单修改 | `Info_PU.ClsS_Infor.Init(login, "普通采购", "CG")`，`VoucherCO_PU.Init(2, login, conn, info, true, "0", "普通采购", 0, "", "CG")`，`bOutTrans=true`；`GetVoucherDataById(头, 体, "", id, "")` by-ref `{0,1,4}` 读入，`editprop=M`，`VoucherSave2(头, 体, (short)1, "<ID>")` by-ref `{3}` | 返回 null 为成功，`@@TRANCOUNT` 仍为 1；U8 按数量差调整订单行 `iArrQTY`，不重算金额（桥按订单修改的算法算）。`sBillType` 传空串时报「类型不匹配」 |
| 到货单 ← 采购订单 | `VoucherCO_PU.Init(2, login, conn, info, true, "0", "普通采购", 0, "", "CG")`。空白 DOM 取 `GetVoucherDataById(头, 体, "", 0, "")` 的 schema 再挂行。`GetVoucherNO(头, "26", err, no)`。行上写 `corufts`（不在 schema 中也照写，U8 回写订单时用它做并发核对）。`VoucherSave2(…, 2, "")` | 第 6 个参数 `sBillType` 必须为 `"0"`。传空串时保存也成功，但 U8 不写临时明细表，订单累计到货 `iArrQTY`、`fPoArrQuantity` 不回写，之后按 U8 规则也删不掉 |
| 无来源到货单 | 同到货单 ← 采购订单的 `Init`、空白 DOM、取号、`VoucherSave2(…, 2, "")`；表头不写 `cpocode`，表体不写 `iposid` / `cordercode` / `corufts`，价税由桥按采购公式算 | 采购选项 `PU.bPTHavePO` 为 True 时桥 409（与无来源采购入库同一选项）。U8 要求部门（「部门不能为空」），桥取请求或供应商分管部门 |
| 采购发票 ← 采购入库单 | `Info_PU.ClsS_Infor.Init(login, "普通采购", "CG")`；`VoucherCO_PU.Init(4, login, conn, info, true, 单据键, "普通采购", 0, "", "CG")`，`bOutTrans=true`。单据键 `purbill`（专用）或 `ppurbill`（普通），不是 `01` / `02`。空白 DOM 同到货单。`VoucherSave2(…, 2, "")` | 新行写 U8 加载出的发票的整套属性，布尔写 `True` / `False`。普通发票：税率 0、`idiscounttaxtype=1`、`btaxcost=False`。U8 回写入库行 `iSumBillQuantity` 和订单行 `iInvQTY` |
| 采购发票删除 | 同一 `Init`，`GetVoucherDataById(头, 体, "", PBVID, "")` 读入，`Delete(头, 体)` | 累计开票数回退 |
| 采购发票复核、取消复核 | 同一 `Init`，`SetVerifyMode(true)`，`GetVoucherDataById(头, 体, "", PBVID, "")` by-ref `{0,1,4}` 读入；复核 `ConfirmBill(头, false)` by-ref `{1}`（第二个参数为 `out bTask`），取消复核 `CancelconfirmBill(头)` | 空串为成功，否则为 U8 原文。复核写 `cVerifier`、`cAuditDate`、`cAuditTime`、`iverifystateex=2`（条件带 `isnull(cVerifier,'')=''`），取消复核清掉。两者在桥的事务里可回滚。这是采购管理的复核，不是应付审核（见「发票的应付审核、应收审核」） |
| 红字采购发票 ← 红字采购入库单 | 同蓝字，只把 `VoucherCO_PU.Init` 第 5 个参数 `bPositive` 换为 false（`sBillType` 仍为 `purbill` / `ppurbill`，VT 4）；表头 `bnegative=True`，行 `ipbvquantity` 和金额为负，`rdsid` 为红字入库行 `AutoID`，无订单行时不写 `iposid` / `cordercode`。`bNegative=1` 的发票删除、复核、取消复核同样用 `bPositive=false` | 红字入库行开票后 `iSumBillQuantity` 为负，等于发票行数量（桥按此核对）。组件无 `GetNegaVouchData` |

- 用视图 `where 1=2` 做采购发票的空白 DOM，`VoucherSave2` 报「类型不匹配」；单据键写错报「列前缀 '' 无效」。
- U8 有时在 `VoucherSave2` 里自行提交事务（本连接 `@@TRANCOUNT` 回到 0）。回写核对要放在提交之前，并处理这种情况（见 `architecture.md`）。
- 采购订单模板号 `ivtid` 用 `GetDefaultVTID(conn, "88")` 取。
- 请购单（卡片 27，`PU_AppVouch` / `PU_AppVouchs`，视图 `pu_AppHead` / `pu_AppBody`，缺省模板 8171）：`Init` 的 VT 为 0，`sBillType` 空串；读取、删除同订单（`GetVoucherDataById`、`Delete`）；审核 `ConfirmApp(头, DomMsg)` by-ref `{1}`，弃审 `CancelconfirmApp(头, 体)`；整单关闭 `CloseApp(头)`、打开 `OpenApp(头)`；新增 `GetVoucherNO(头, "27", err, no)` 后 `VoucherSave2(…, 2, "")`，空白 DOM 同采购发票。表体数量列 `fquantity`；金额列：`ioricost` 原币单价、`ioritaxcost` 原币含税单价、`iorimoney` 原币金额、`ioritaxprice` 原币税额、`iorisum` 原币价税合计、`funitprice` / `ftaxprice` 本币单价、含税单价、`imoney` 本币金额、`itaxprice` 本币税额、`fmoney` 本币价税合计。卡片必输：`ccode`、`ddate`、`fquantity`、`drequirdate`、`cexch_name`、`iexchrate`。

### 采购结算

采购结算单为卡片 99（`PurSettleVouch` / `PurSettleVouchs`），把发票行（`iBsID` → `PurBillVouchs.ID`）与入库行（`iRdsID` → `rdrecords01.AutoID`）配对，没有审核。组件同发票：`VoucherCO_PU.clsVoucherCO_PU`。以下调用都在调用方事务里，可回滚。

- **自动结算一张发票**：`Info.Init(login, "普通采购", "CG")` by-ref `{0}`；`co.Init(4, login, conn, info, True, "purbill", "普通采购", 0, "", "CG")`（普通发票用 `ppurbill`）；`co.bOutTrans = True`；`GetVoucherDataById(头, 体, "", PBVID, 定位串)` by-ref `{0,1,4}`；`CheckSettle(sErr)` 返回 True；`bRdBVAutoSettle(头, 体, "", "")` 返回空串为成功，非空为 U8 错误原文。结果：新 `PurSettleVouch`（`PSVID` = 原最大值 + 1，`cSVCode` 为 15 位补零的 `PSVID`），每个发票行一行 `PurSettleVouchs`（`iRdsID` = 发票行 `RdsId`，`iBsID` = 发票行 `ID`，`iSVQuantity` = 发票数量，`iSVPrice` = 行金额），`PurBillVouch(s).dSDate` = 登录日期，`cPBVVerifier` 不变（不做应付审核）。
- **末尾两个 OUT 串必须传空串**：`bRdBVAutoSettle` 的 `sPbvsRdsRltTbl`、`sTmpPBVcode` 按值传 `""`。传非空串时 U8 走合并开票的临时表分支，对整账套执行 `SELECT … into tempdb..PUTmp_BillSum…`，耗时极长。也不要设 `m_TmpTableName`。U8 内部调用存储过程 `PU_AutoSettleBillRefRD`（`@cTmpTable=''` 时只处理这一张发票，过程内无 `COMMIT`）。
- **登录日期**：登录日期所在会计月之前的月份采购必须已结账，否则 U8 拒绝「当前登录日期所在的会计月以前的月份未结账，不能结算」，桥作 409 `u8_rejected`。请求可带 `head.settle_date`（或 `date`）把登录日期设到上月最后一天。
- **删除**：`co.Init(5, login, conn, info, True, "", "普通采购", 0, "", "CG")`，`bOutTrans = True`，`GetVoucherDataById(头, 体, "PSVID=<id>", PSVID, 定位串)` by-ref `{0,1,4}`，`Delete(头, 体)` 返回空为成功。表头视图的主键属性为 `psvid`。
- 不要用 `PU_AutoSettle*`（处理整账套或自行 `COMMIT`）、`PU_AutoSettleSingle*`（自行提交，按数量游标配对）。手工结算见下一节。

### 采购手工结算

- **没有可调用的组件。** `VoucherCO_PU.DoSettle` 的 `vType=7`（`PU_ManSettle`）在本版本中是空操作：返回成功（`sErr` 空）但不写库；只有 `vType=6`（代管）会生成结算单。`Init(5)` + `VoucherSave2(头, 体, 2, "")` 带卡片 99 的 DOM 返回 null，同样不插入任何数据。U8 手工结算界面自行 `INSERT` 结算单，再调存储过程回写；桥执行相同的 SQL（与 U8 界面执行的 SQL 一致，实测核对）。属第一级（常规）写入。已与 U8 做的配对、红蓝入库对冲、红蓝发票对冲、多发票结算单逐一比对结算行、入库行已结算数量与单价、发票结算日期。
- **结算单的形状**：表头 `PurSettleVouch` 的 `cSVCode` 为 15 位补零的 `PSVID`，`cSettleType='01'`，`cBusType`、`cPTCode` 取自发票，`dSVDate` 为结算日期，`cMaker` 为操作员**姓名**；表体 `PurSettleVouchs` 每个配对一行：`iRdsID` 入库行（红蓝发票对冲为 0）、`iBsID` 发票行（红蓝入库对冲为 0）、`cPIVCode` 入库单号（`RdRecord01.cCode`）、`cBillCode` 发票号（`cPBVCode`），`iSVQuantity` 数量，`iSVCost` 无税单价、`iSVPrice` 无税金额（`iSVACost` / `iSVAPrice` 同值），`iTax` 0，`iSum` 金额，`cbvcvencode` / `crdcvencode` 为供应商，配对行 `cUpSoType='01'`。主键按 U8 的取号过程 `PU_GetID` 取。
- **入库行回写 `PU_SettleWriteBKRDS`**：每个入库行调一次（`@RdsID`、本次数量 `@iRDQuan`、金额、单价等）。回写成本开关 `@bWBCost` 打开时，过程在 `@iRDQuan` 与 `@iRDQuanCheck`（按账套数量小数位格式化）相等时，按该入库行全部结算行的金额改写其 `iPrice`、`iUnitCost`、`iAPrice`（原值备份到 `ST_RdRecords_bak`）；不相等时不改价。已结算数量 `iSQuantity` 每次累加。U8 自动结算传的是入库行剩余未结算数量，即只在本次结清入库行时才改价；桥同样传锁内读到的未结算数量。
- 其余回写同 U8：发票行结清时写 `dSDate`，配对的发票行写 `dInDate`，发票全部行结清时写表头 `dSDate`，`Inventory.iInvNCost` 写配对行的结算单价（赠品不写）。删除用组件删除（`Init(5)` + `Delete`），按行回退，对手工结算单同样适用。
- 同一发票行既做发票对冲又与入库配对时，税额无法按 U8 的方式分摊，桥拒绝。

### 销售订单 / 采购订单锁定

锁定人在表头 `cLocker`（`SO_SOMain`、`PO_Pomain`，nvarchar(20)），写操作员姓名，空为未锁定。视图 `SaleOrderQ`、`zpurpoheader` 中为 `clocker`。没有 `bLocked` 之类的列；`DispatchList` / `PurBillVouch` 的 `iNetLock` 是网络并发锁，与此无关。

- 销售订单：`SaSession.OpenSa(conn, login, 12, …)`，表头 DOM 读 `select '' as editprop, saleorderq.* from SaleOrderQ saleorderq where saleorderq.id=?`（同整单关闭），`LockVouch(头, bLock)`，只有 `{0}` 按引用。返回空串或 null 为成功，锁定后 `cLocker` 为当前操作员姓名。在桥的 `CoTrans` 里调用，可回滚。U8 拒绝锁定已审核的订单（「销售订单已经审核!」）；解锁未锁定的订单回「当前单据没有锁定！」。第三个参数 `iSID` 为按行锁定，桥不用。
- 采购订单：本版本无可用的锁定入口，桥不支持，`vouchers/lock` 对采购订单 400。`VoucherCO_PU` 的 `DoLock(conn, 头, 体, sVouchType, sErr)` / `RemoveLock(…)`（`sErr` by-ref `{4}`）对已审核、未审核订单都回 false（「本张已经被修改,不能锁定.」或空错误）且不写 `cLocker`；官方 `U8API/PurchaseOrder/Lock` 指向的 `LockVouch` 在采购组件上不存在（`DISP_E_UNKNOWNNAME`）。U8 客户端锁定的采购订单，桥仍按 `cLocker` 挡住其他操作员的修改、删除、审核。
- 功能 id（UFMeta `AA_FormButtonAuths`）：销售订单卡片 `SA_17voucher` 的 `lock` / `unlock` 为 `SA03010108` / `SA03010109`；采购订单卡片 `pu_frms_voucher_88` 的 `Lock` / `removelock` 为 `PU0311` / `PU0312`（桥未登记）。
- 列宽 nvarchar(20)：姓名超过 20 个字符时，桥按截断后的值比较锁定人；U8 对超长姓名截断还是报错未验证。
- 桥在修改、删除、审核、弃审前按 `cLocker` 挡住其他操作员，只允许锁定人本人解锁。U8 的 `Save`、`VerifyVouch`、`Delete` 是否自行拒绝被锁定的订单、其他操作员能否在 U8 中解锁，未验证。

## 8. 库存

登录 `ST`，`USERPCO.VoucherCO`，`IniLogin` 之后调用。单据类型短码：采购入库 `01`、其他入库 `08`、其他出库 `09`、产成品入库 `10`、材料出库 `11`、调拨 `12`、销售出库 `32`；形态转换 `15`、调拨申请 `62`、盘点 `18`、货位调整 `19`、期初结存 `34`。

| 操作 | 调用 | 要点 |
| --- | --- | --- |
| 读取、审核、弃审、删除 | `Load`、`Verify` / `UnVerify` / `Delete`，都只传到 `bList`，共 9 个参数 | 仓库启用货位时 U8 拒绝没有货位的单据；库存不足有专门提示 |
| 新增 | `Insert`，13 个参数；新主键在下标 6 | 空白 DOM 用各单据的表头表体视图 `where 1=2` |
| 修改 | `Load(type, "id=<id>", 头, 体, pos, err, false, "")`（`pos` 可能为 null，换成空 DOM），`Update(type, 头, 体, pos, err, conn, msg, true, true, false, "", true)` | 返回布尔，错误文本在第 5 个参数。U8 自行写修改人、修改日期 |
| 销售出库修改 | `IniLogin(login, "")` by-ref `{1}`；`Load("32", "id=<id>", …)` by-ref `{2,3,4,5}`，表头和行 `editprop=M`；`Update("32", …)` 同上 | 返回 true，留在请求连接的事务里；U8 按数量差调整发货行 `fOutQuantity` |
| 调拨单审核 | `Verify("12", id, err, conn, ufts, msg, true, true, false, new DispatchWrapper(null), "", dict)`，`dict` 为 `Scripting.Dictionary` | `dict.Keys` 是生成的其他出库和其他入库单主键。弃审用 9 参数版本，会删除这两张未审核的生成单 |
| 采购入库 ← 采购订单 | 视图 `zpurrkdhead`、`zpurrkdtail`，`Insert("01", …)`。表体 `iposid` 为订单行主键 | CO 回写订单行累计入库 `iReceivedQTY` |
| 采购入库 ← 来料检验单 | 同上，行上带 `icheckidbaks`、`iarrsid`、`ccheckcode`；货位仓库另带 `cPosition`，货位 DOM 传空（参照采购订单、采购退货单同样） | `Insert` 不检查货位：货位仓的行不带 `cPosition` 也照存，`cPosition` 为 NULL、不写 `InvPosition`，所以桥生单前自行核对。U8 回写检验单累计入库、到货行和订单行累计数，并写 `InvPosition`；删除时先清货位再删，全部回退 |
| 红字采购入库 ← 采购退货单 | 同上，`Insert("01", …)`；表头 `bredvouch=1`、`csource=采购到货单`、`carvcode` / `darvdate` / `ipurarriveid` 指向退货单，行 `iarrsid` = 退货单行 `Autoid`、`cbarvcode`、有订单行时 `iposid` / `cpoid`，`corufts` 取退货单表头 `ufts`，`iquantity` / `inquantity` 和金额为负 | U8 待入库视图 `pu_v_preparestockbyarrforst` 对退货行按 `iQuantity - fValidInQuan - fInValidInQuan`（负数）算可入库，U8 回写退货行 `fValidInQuan`（负）；订单行 `iReceivedQTY` 是否减少未验证（桥只记审计）。`ST.VouchSource` 无「采购退货单」，来源写「采购到货单」 |
| 采购入库 ← 蓝字到货单 | 同上，`Insert("01", …)`；表头 `csource=采购到货单`、`carvcode` / `darvdate` / `ipurarriveid` 指向到货单、`bredvouch=0`，`cordercode` 只在各行属同一张订单时写；行 `iarrsid` = 到货行 `Autoid`、`cbarvcode` / `dbarvdate`、有订单行时 `iposid` / `cpoid`，单价、税率取到货行（`iOriCost` / `iOriTaxCost` / `iTaxRate` / `bTaxCost`），批号缺省为到货行批号，保质期与自由项沿用到货行；不写 `corufts` | 与 U8 界面生成的入库一致（`iarriveid` 为空）。U8 回写到货行 `fValidInQuan`（累计入库），订单行写 `freceivedqty`。剩余可入库 = U8 视图 `pu_arrbody.fininquantity`（`iQuantity - fRefuseQuantity - fValidInQuan - fInValidInQuan`）。「普通业务必有订单」打开且订单已有到货单时，参照订单生成入库 U8 报「该操作会造成订单到货和入库同时存在」。未经完整实测 |
| 采购入库（无来源） | 同上，表头 `csource=库存`、`cbustype=普通采购`、`vt_id=27`，表体不带来源行 id；`Update("01", …)` 同其他入库 | 无回写；价税由桥按采购公式算（见「金额」）。表头 `itaxrate` 可为空；采购选项 `bPTHavePO` 为 True 时 U8 拒绝（「普通采购必有订单，存货[x]不能手工录入」）；货位仓库审核时要求行上 `cPosition`，行上带 `cPosition`、货位 DOM 传空时 U8 在保存时写 `InvPosition`。带生产日期和失效日期时 U8 原样保存，并从档案带出 `iMassDate`、`cMassUnit`；不补「有效期至」`dExpirationdate` / `cExpirationdate` |
| 采购入库（无来源红字） | 同上，表头 `bredvouch=1`，表体 `iQuantity`、`iNum`、`iOriMoney`、`iOriTaxPrice`、`ioriSum`、`iPrice`、`iTaxPrice`、`iSum` 为负、单价为正（与 U8 客户端手工红字退库一致：`cSource=库存`、`bRdFlag=1`、`VT_ID=27`），`Insert` 第 11 个参数 `bIsRedVouch=true` | 无回写。「普通业务必有订单」（`PU.bPTHavePO`）只拦蓝字，U8 对红字退库不执行该选项；桥同样放行红字，蓝字 409 |

### 销售出库按行部分出库

先把一张出库单改小再 `MakeOutVouch`，U8 会把原剩余数量整份再出一次（选项 `bOverDispOut=True` 时超发），所以不能靠多次 `MakeOutVouch` 做部分出库，部分出库之后的整单生成也会超发。另外 `USERPCO` 的 `Load` 走 U8 自己的连接：放进桥未提交的事务里，读不到刚生成的单据，或与本事务互相锁住。因此桥分两步，不是一个事务：

1. 读发货行 `iQuantity`、`fOutQuantity`、`cSCloser` 作基线（已关闭的行不算应出，`lines` 列出它时 409「明细行已关闭」）；`MakeOutVouch` 整单生成，在桥的事务里（同一连接传给 `cnnFrom`）调用并由桥提交（调用后 `@@TRANCOUNT` 仍为 1，U8 不自行提交）。这一步出的是整张发货单的剩余，与 `lines` 无关：任一未出完的行不能整行出库（库存不足、货位、批次），`MakeOutVouch` 就整张失败，请求 409（如 `stock_shortage` 报另一行的存货），即使那一行未被请求。
2. 按发货行比较 U8 生成的数量（`rdrecords32` 按 `iDLsID` 汇总）与应出数量：带 `lines` 为请求数量，不带为 `iQuantity − 基线 fOutQuantity`。每行都不多则结束（带 `lines` 时生成少于请求数量算失败）。有多出时：事务外对每张新单 `Load("32", "id=<id>", …)`，标好改动后开新事务 `Update("32", …)`（同上表「修改」，by-ref `{4..8}`，不自行提交，U8 按差额调整 `DispatchLists.fOutQuantity`）：要少出的行改为应出数量（`iNum` 按换算率重算，有 `iUnitCost` 时 `iPrice = round(数量 × iUnitCost, 2)`），不要的行 `editprop=D`；一行都不要的整张走普通删除（9 参数 `Delete`，有货位记录时先 `ClearPosition` 再 `bList=true`，否则 `bList=false`，见「货位」）。要改数量的单据有货位记录时不改，按失败处理。提交前核对每个发货行 `fOutQuantity = 基线 + 应出`。

第 2 步任何失败都补偿：新开事务删除第 1 步生成且仍存在的出库单，核对发货行 `fOutQuantity` 回到基线，返回 409 `u8_rejected`「已撤回生成的销售出库单：<原因>」。补偿也失败时返回 504 `outcome_unknown`，消息和审计中列出生成的出库单 id，需人工核对。完整的两步流程、`editprop=D` 删行、补偿及跨仓库多张出库单的情形未验证，只验证了单步操作。

### 销售出库指定批号、货位

`MakeOutVouch` 没有表头表体覆盖参数（第二个参数是输出的单号集合），U8 按发货行带出仓库、批号、货位。因此桥在第 2 步（按数量改回）之后再加一步，走同一改单路径（事务外 `Load("32")`，新事务里 `Update("32")`）：

- 该发货行的第 1 行改为请求的第 1 次：`iQuantity`（有 `iNQuantity` 列时一并改，辅数量按换算率、有单位成本时金额按数量重算）、`cBatch`、`cPosition`，`editprop=M`；请求次数多于行数时 `cloneNode(true)` 这一行追加（保留 `iDLsID` 等来源关联和单价，清掉 `AutoID`、`cbsysbarcode`，`irowno` 顺延，`editprop=A`）；行数多于次数时多出的行 `editprop=D`。表体 `iDLsID` 不唯一，一个发货行对应多行出库是 schema 允许的。
- 换批号的行（含克隆行）先清 `cBatchProperty1..10`、`dMadeDate`、`dVDate`、`iMassDate`、`cMassUnit`、`cExpirationdate`、`dExpirationdate`、`iExpiratDateCalcu`，再按 `AA_BatchProperty`（`cInvCode`、`cBatch`、`cFree1..10`）填 `cBatchProperty1..10`；日期属性按 `yyyy-mm-ddThh:mi:ss` 写。改数量时 `iNNum` 按 `iNQuantity` 和换算率重算。只带数量的请求行不动。
- 货位 DOM 传空，U8 保存时写 `InvPosition`；提交前核对带 `cPosition` 的行在 `InvPosition` 中有同货位记录，否则回滚。
- 单据已有 `InvPosition`（发货行带了货位）时不改，按失败补偿；先 `ClearPosition` 再 `Update` 的改法未验证，桥不实现。
- 预检的可用量公式见「SQL」一章；货位存量取 `InvPositionSum.iQuantity`（按仓库、货位、存货、批号、自由项）。U8 保存时的库存检查仍可能拒绝（409 带原文，桥补偿）。

### 货位

- 带 `cPosition` 保存的单据，U8 在保存时写 `InvPosition`（每行一条），审核、弃审正常。`InvPosition.cvouchtype` 与单据类型短码相同（如 `01`、`09`、`32`、`19`、`34`）。
- 对有 `InvPosition` 行的单据调 `USERPCO.VoucherCO.Delete` 且 `bList`（第 9 个参数）为 false 时，U8 在服务进程里弹模态界面卡死（`bCheck`、`bBeforCheckStock`、`DelSnAndPosFlag` 取任何值都一样），桥靠看门狗 3 分钟后退出。有货位记录的单据永远不要用 `bList=false` 删除。
- 正确做法：在本连接的事务里先 `ClearPosition("<RdID>", err, "<短码，如 08>", conn)`，by-ref `{1,2,3}`，返回 true、`err` 为空，该单据的 `InvPosition` 行被删、表体 `cPosition` 被清空；再 `Delete(type, id, err, conn, ufts, msg, true, true, true)`（`bList=true`），返回 true，单据被删，`InvPositionSum` 回退；不卡、不自行提交。`ufts` 用表头视图的 `ufts` 串，只去右侧空格。不先 `ClearPosition` 直接 `bList=true` 删除，U8 回「存货[..]已经指定了货位,请先修改存货的货位数量.」。`Web_StkDelete` 返回 true 但不删除任何数据，不要用。
- 桥删除各类入出库单时按 `RdID` + 短码查 `InvPosition`，有行才 `ClearPosition` 并用 `bList=true`，否则 `bList=false`；`ClearPosition` 失败回滚 409 `u8_rejected`，提交前确认货位记录和表头都已删除。
- 桥拒绝的改法（U8 是否支持未验证）：`Update` 改已有行的货位（400）；删除带货位的行、改带货位行除备注和自定义项以外的字段、给有货位记录的单据换仓库（409）。调拨弃审删除生成的其他入出库单时，若生成单有货位记录是否同样卡住，未验证。

### 形态转换单、调拨申请单、盘点单

与调拨单同一个 `USERPCO.VoucherCO`、同一组参数：

- 短码与卡片：形态转换 `15`（卡片 0305，`AssemVouch` / `AssemVouchs`，同表还有组装 13、拆卸 14，按 `cVouchType` 区分），调拨申请 `62`（0324，`ST_AppTransVouch` / `ST_AppTransVouchs`），盘点 `18`（0307，`CheckVouch` / `CheckVouchs`）。缺省模板 `VT_ID` 91、37、29。视图 `AssemM` / `AssemD`、`transrequestm` / `transrequestd`、`checkm` / `checkd`，表头视图的 `ufts` 同 `transm` 已是字符串。
- 审核人：形态转换、调拨申请为 `cVerifyPerson` / `dVerifyDate`；盘点为 `cAccounter` / `dveridate`（不是 `cVerifyPerson`）。
- 形态转换的表体 `bAVType` 是文本「转换前」/「转换后」，按 `iGroupNO` 成组；仓库在行上，表头无仓库。审核生成转换出库（09，收发类别「转换出库」）和转换入库（08，「转换入库」），两张的 `cSource=形态转换`、`cBusType` 为转换出库 / 转换入库，`cBusCode` 为形态转换单号；表体 `RdsID` 审核后仍为空，按表头 `cBusCode` 关联。`bTransFlag` 审核后仍为 0，不能作审核标志。形态转换审核用 12 参 `Verify` 并从字典读出生成单，弃审删除未审核的生成单。
- 调拨申请审核只写审核人，不生成调拨单，也不写核准数量。下游调拨单行 `TransVouchs.iTRIds` 指向申请单行 `autoID`，生成调拨时累加 `iTvSumQuantity` / `iTVSumNum`；关闭人在 `cCloser`，行关闭人 `cBCloser`。
- 盘点：U8 不允许同一仓库有第二张未审核的盘点单，也不允许同一张单上存货、批号（自由项、货位、销售订单）都相同的行。`iCVQuantity` 账面、`iCVCQuantity` 实盘；按实盘、账面填 `iAdInQuantity` / `iAdOutQuantity`（盘盈 = 实盘 − 账面、盘亏 = 账面 − 实盘，取大于 0 的一边，另一边 0）和对应件数，桥新增时照填。盘点账面数量桥按 `CurrentStock` 的仓库、存货、批号、自由项合计，货位仓库里行不分货位，U8 保存、删除都接受；U8 界面是否按货位细分未对照。
- 盘点审核没有可用的无界面入口，桥不开放审核、弃审，请在 U8 客户端审核：`Verify("18", …)` 9 参和 12 参都回「类型不匹配生单时出错」（生成盘盈 / 盘亏其他出入库单时失败）。原因是第 10 个参数 `MakeWheres` 声明为 VB6 `Collection`（按引用），传 `Nothing` 在调度层即 `DISP_E_TYPEMISMATCH`，而 VB6 `Collection` 没有可创建的 ProgID，组件外无法构造。U8 客户端审核的盘点单生成的 08 / 09，桥按 `cSource`、`cBusType`、`cBusCode`「盘点」「盘盈」「盘亏」识别。
- 单号：U8 编号规则按短码 15 / 62 / 18 取卡片 0305 / 0324 / 0307（形态转换审核生成的转换入库、转换出库取 0301 / 0302）；表体不写单号列（`cAVCode` / `cTVCode` / `cCVCode`）时 U8 自行补。

调拨单参照调拨申请单：不用 `MakeTransVouchFromTR`（三个 `out` 参数，依赖界面先把选中行写进临时表），改用调拨单新增的 `Insert("12")`，表体写 `itrids`（`TransVouchs.iTRIds` = `ST_AppTransVouchs.autoID`，同采购入库参照订单的 `iposid`）。U8 保存时按 `itrids` 给申请单行累加 `iTvSumQuantity` / `iTVSumNum`，删除调拨单时退回；桥在同一事务里核对。可参照数量 = 核准数量 `iTvChkQuantity` − 累计调拨（U8 参照列表只列核准数量非 0 的行）。超核准由选项 `ST.bOverTransRequestTransfer` 控制。表头 `ctranrequestcode` 由桥写申请单号。U8 是否在行全部调完后自动写 `cBCloser` 未验证。

### 货位调整单

与形态转换单同一组参数：`Insert("19")` 13 参、`Verify` / `UnVerify` / `Delete` 9 参，都在请求连接的事务里。属第一级写入（受写入策略约束），新增、审核、弃审、删除已实测往返：

- 卡片 0313（`KCAdjust`），表 `AdjustPVouch` / `AdjustPVouchs`，视图 `AdjustPM` / `AdjustPD`（表头视图内连仓库，表体视图内连存货和两个货位档案，表头视图的 `ufts` 已是字符串），缺省模板 `VT_ID` 113。表头主键 `Id`、表体主键 `autoID` 都不是自增列；表体外键 `ID`。审核人 `chandler`、审核日期 `dVeriDate`，另有 `dnverifytime`。`AuditBizObjects` 中有该表（`srcTable=AdjustPVouch`），但未发布审批流（`iswfcontrolled=0`）。表体无 rowversion。
- 行：调出货位 `cBPosCode`、调入货位 `cAPosCode`（两者不同）、数量 `iQuantity`；不用 `RdsID`。带批号、自由项、件数的写法未验证。
- 货位台账：审核时 U8 往 `InvPosition` 写 `cvouchtype='19'`、`cSource='货位调整'` 的行，每个表体行一出（`bRdFlag=0`，调出货位）一入（`bRdFlag=1`，调入货位），`RdID` = 表头 `Id`、`RdsID` = 表体 `autoID`，`dDate` 取单据日期、`cHandler` 为审核人；并更新 `InvPositionSum`。保存时不写台账。不动现存量 `CurrentStock`，不生成其他出入库单。这几张表上无触发器。
- 桥在提交前核对：新增后无台账、结存未变；审核、弃审后审核人、台账行数（审核 2×行数、弃审 0）和各（货位、存货、批号、自由项）结存变动等于调整单（`UnVerify("19")` 未自行删台账、退结存时回滚 409）；删除后表头、表体已删。保质期管理的存货、代管仓、调出货位有 VMI 结存的请求在调用前拒绝。

### 单号

`GetNewVouchCode` 和 `PublicSub.SetBillSerial` 都不套用用户设置的编号规则。正确做法：`USCOMMON.PublicSub.GetBillRuleXml(login, 卡片号)` 取规则，按表头值填各前缀的种子（日期按年月日 / 年月 / 年），再 `UFBillComponent.clsBillComponent.InitBill(UfDbName, 卡片号)` + `GetNumber(规则, true)`。取号会改写传入的 DOM，需另建一份表头取号。取号推进 `VoucherHistory`，事务回滚后号不退回，只留断号。
## 9. 生产

| 操作 | 调用 | 要点 |
| --- | --- | --- |
| 材料出库 ← 生产订单 | 视图 `RecordOutQ` / `recordoutsq`，`Insert("11")`。表体子件分配 `AllocateId`、订单行序号 | U8 回写子件已领量 `IssQty`，允许超领 |
| 产成品入库 ← 产品检验单 | 视图 `recordinq` / `recordinsq`，`Insert("10")`，非合并检验只能 1 行，表体 `imergecheckautoid=-1` | U8 回写订单行合格入库量和检验单累计入库量 |
| 产成品入库 ← 合并检验的产品检验单（`BMERGECHECKFLAG=1`） | 同上，一个合并来源一行：表体 `imergecheckautoid`=`QMMergeCheckDetail.AUTOID`、`impoids` / `cmocode` / `imoseq` 取该来源的订单行（`SOURCEAUTOID`=`MoDId`）、`inquantity`=该来源 `FREGQUANTITY`，`icheckidbaks` 仍是检验单 `ID`；表头不写 `cmpocode`。`imergecheckautoid` 不在卡片 0411 的 `voucheritems` 里，但在 `RecordInSQ` 视图里 | 合并来源在 `QMMergeCheckDetail`（按来源存 `FREGQUANTITY` / `FCONQUANTIY` / `FSUMQUANTITY` / `BPROINFLAG` / `SOURCEAUTOID`）；表头 `SOURCEAUTOID` 只是其中一个来源（可能为 0）。入库时 U8 回写来源 `FSUMQUANTITY`、表头 `FsumQuantity`（= 来源合计）、订单行 `QualifiedInQty`，满额时来源 `BPROINFLAG=1`；删除时 `Delete` 自己回退。桥提交前在同一事务里核对三处都加了本次数量，删除时核对减回，不符回滚 409。修改时的回写未验证，桥拒绝修改这类入库单 |
| 产成品入库 ← 产品不良品处理单（QM06） | 同上，表头 `csource=产品不良品处理单`；表体 `irejectids`（处理单表体 `AUTOID`）、`crejectcode`、`icheckidbaks` / `ccheckcode`（处理单的检验单）、`impoids`（订单行 `MoDId`，取法同 U8 视图 `QM_RefProReject`）；存货、数量用处理后存货 `CDIMINVCODE`、处理后数量 `FDIMQUANTITY` | 处理单没有累计入库数列，已入库数按 `rdrecords10.iRejectIds` 汇总；U8 入库后置 `QMREJECTVOUCHERS.BFLAG=1`，订单行 `QualifiedInQty` 含不良品入库量。U8 只接受一次入库全部处理后数量（部分入库报「入库数量合计必须等于降级后数量」）。合格入库量达订单数量时 U8 自动关闭订单行（`Status=4`、`CloseUser`、`CloseDate`），之后删除入库单要先打开订单行 |
| 产成品入库 ← 生产订单 | 同上，表头 `csource=生产订单`，表体 `impoids`=订单行 `MoDId`、`inquantity`=订单数量，不写 `icheckidbaks` / `ccheckcode` | `QualifiedInQty` 等于 `rdrecords10` 按 `iMPoIds` 的合计；`ST.bOverMPIn`=True 时允许超订单数量入库（上限看存货 `fInExcess`）。订单行 `QcFlag=1` 且 `ST.bQuality`=True 时须经产品检验单入库，桥 409。桥在同一事务里核对 `QualifiedInQty` 加 / 减本次数量。表头只写 `cmpocode`、不写 `iproorderid` / `cpspcode` 可以保存 |
| 材料出库（无来源） | `Insert("11")`，表头 `csource=库存`、`cbustype=领料`、`vt_id=65`，表体不写 `impoids` | 不回写子件。库存选项 `ST.ballowAddnewVouch`（U8 中「领料必有订单」）为 True 时 U8 拒绝无来源领料（USERPBO 资源 00690），桥先 409；为 False 时可无来源新增 |
| 两者删除 | 库存通用 9 参数 `Delete` | U8 回退上述累计数 |
| 生产订单审核、弃审 | 登录子系统 `MO`。每次新建 `UFIDA.U8.U8APIFramework.U8EnvContext`（设 `U8Login`）和 `U8ApiComBroker`：`Connect("U8API/MOrder/MOrderAuditing"或"MOrderUnauditing", env)`、`AssignNormalValue("mocode", 订单号)`、`InvokeApi()`、`GetLastError()`、`Disconnect()` | API 自己开事务，不要再包一层。`GetLastError` 取第一行、去掉 .NET 异常类型名。订单行状态看 `mom_orderdetail.Status`（3 审核，1/2 未审，4 关闭）；重复审核 U8 只回「没有资料需要处理」，调用前自己判断 |
| 生产订单关闭、打开 | 没有 U8API（`IB_AppTag` 只有新增、审核、弃审、读取、修改、删除），与 U8 界面执行的 SQL 一致（实测核对）：在请求连接的 `CoTrans` 里 `CREATE TABLE #tmp_procmodid (MoDId int, Ufts varchar(30), ProcDate datetime, ErrFlag int, Errno int)`，每行插入 `MoDId`、`convert(char, convert(money, Ufts), 2)`（不去前导空格）、登录日期、`1`、`1`，再 `EXEC Usp_MO_Close @ModifyDate, @ModifyUser, '<ALL>', '', '<ALL>', '<ALL>', '<ALL>', @v_closeflag` 或 `Usp_MO_UnClose`（少最后一个参数），然后读每行 `ErrFlag` / `Errno` | 存储过程见到 `@@TRANCOUNT>0` 不自己开事务，回滚有效。`ErrFlag=0` 成功，行 `Status` 3→4、写 `CloseUser` / `CloseDate`；打开 4→3、清空。`Errno`：101 登录日期所在月已做成本计算（`CaF_IsCalculate`，直接返回），102 有在制品（只在 `@v_closeflag=0` 时），0 不在权限串内（打开时恢复子件预留后预留量超过现存量也记 0），1 未处理（`Ufts` 不符）。`@v_closeflag` 缺省 1（允许关在制订单），桥传 0。`@ModifyUser` 是 `varchar(20)`，被拼进动态 SQL 的字符串常量，含单引号的操作员桥直接拒绝。`CloseUser` 记操作员编码（与 `RelsUser` 一样），不是姓名。权限串传 `<ALL>`，权限由桥判断。建临时表那句不带参数（直接批处理），否则建在 `sp_executesql` 作用域里 |

生产订单审核需要 U8 的生产制造服务（`U8MPool`）在运行，否则 U8 报「连接到 IPC 端口失败」。`U8MPool`、`U8SCMPool`、`U8TaskService` 在某些管理操作（例如重设数据库 `sa` 口令）之后可能停下，要检查。

### 生产订单新增与删除

U8 API 的行为（已在测试账套实测）：

- **新增** `MOrderAdd`：登录子系统 `MO`，env 与 broker 同审核。`Connect("U8API/MOrder/MOrderAdd", env)` **之后**才取扩展实体（之前自建 `ExtensionBusinessEntity` 不可用；`AssignExtBOValue` 只能填表头）：`ext = GetExtBoEntity("extbo")`、`head = ext.NewItem()`、`line = head.GetSubEntity("Mom_OrderDetail").NewItem()`，`line.SetValue` 填 `DSortSeq`（从 1 起）、`DMoClass`（1）、`DInvCode`、`DQty`、`DStartDate` / `DDueDate`（`yyyy-MM-dd`）、`DMoTypeCode`、`DMDeptCode`，可选 `DWhCode`、`DRemark`；`head.SetValue("MoCode", 单号)` 省略时 U8 按 `MO21` 规则编号。`InvokeApi()` 为 true、`GetLastError()` 为空即成功；API 自己开事务（`TransactionScope`）提交，不要包 `CoTrans`。
- **新增结果**：`mom_order`、`mom_orderdetail`（`Status=2`）、`mom_morder`（开工、完工日期），`CreateUser` 是操作员编码。不送 `Mom_MoAllocate` 时 U8 按标准 BOM 展开子件（`mom_moallocate`，数量 = `BaseQtyN / BaseQtyD × 数量`）。新 `MoId` 不在 `head.GetValue("MoId")` 上，要在 `InvokeApi` 之后 `ext.Serialize()` 从 `<mom_order moid="…">` 取。没有物料清单的自制件由 U8 拒绝。
- **删除** `MOrderDelete`：`AssignNormalValue("mocode", 单号)`、`InvokeApi()`。未审核的订单删掉 `mom_order`、`mom_orderdetail`、`mom_morder`、`mom_moallocate`（`Usp_MO_Del` 只删 `Status` 1 / 2 的行）；已审核的返回 false，`GetLastError` 是「审核或关闭的生产订单不可删除!」接堆栈，取第一行并在「 在 」「UFSoft.」处截断。弃审后可以删除。
- `MOrderUpdate` 不带 `Mom_MoAllocate` 时**删除全部已有子件、不重新展开**（见「生产订单修改」）。

桥的做法（`MoCreate`、`MoDelete`）：

- 新增前查库：存货存在、是自制件（否则 400）、未停用；订单类别存在；部门存在且是末级；仓库存在且未停用；给了单号时不重复；数量小数位不超过 `iStrsQuanDecDgt`（否则 400）。U8 拒绝时 409 `u8_rejected` 带原文。销售订单关联字段（`DOrderType` / `DOrderCode` / `DOrderSeq`）不收。
- 结果确认：拿到 `MoId` 时在新连接上核对该单和 `CreateUser`。调用异常或拿不到 `MoId` 时，只认「`MoId` 大于调用前 `max(MoId)`、`CreateUser` 是本操作员、创建时间不早于调用前的数据库时间、每一行的序号、存货、数量、部门、类别、仓库、备注、起止日期（和单号）都相同」的唯一一张；否则 IPC 错误 503、其余 504 `outcome_unknown`。
- 删除前查库：各行 `Status` 只能是 1 / 2；`IsWFControlled=1` 且 `iVerifyState=1` 拒绝；被材料出库（`rdrecords11.iMPoIds` = 子件 `AllocateId`）、产成品入库（`rdrecords10.iMPoIds` = 订单行 `MoDId`）引用拒绝；集合生产订单（`CollectiveFlag<>0`，U8 会连带删子订单）拒绝，请在 U8 客户端删。
- 功能权限：新增 `MO02001N`，删除 `MO02001D`（卡片）或 `MO03001D`（列表）（UFMeta `AA_FormButtonAuths`；`MO03009*` / `MO02009*` 是补料申请单）；数据权限按行的存货、部门、仓库，同关闭。

### 生产订单修改

`MOrderUpdate` / `MOrderLoad` 的行为（已在测试账套实测）：

- `MOrderUpdate`（参数 `extbo`，`uapMetaID=11`）只按表头 `MoCode` 找单，表头其他字段（含 `Define_*`）一概不写。行按 `DSortSeq` 对：找得到就替换字段，找不到就当新行；不删行。`DInvCode` 与库里不同抛 `MO.Upd.PartNotSame`；`DStartDate`、`DDueDate`、`DQty`、`DMrpQty`、`DAuxQty`、`DLeadTime`、`DWIPType`、`DOpScheduleType` 非 null 就写；`DMoTypeCode`、`DWhCode`、`DMDeptCode`、`DRemark` 等字符串只在非空时写（清不空）；`DDefine_22`…`DDefine_37` 非 null 时写入。
- 子件：**先删除该行全部子件，再按送来的 `Mom_MoAllocate` 新建**（没送就只删不建）。新子件 `AllocateId`、`Ufts` 换新，`OpComponentId` 变 0，`VirOpComponentIds`、`cSubSysBarCode`（`||MO21|<单号>|<行号>|<子件行号>`）变 NULL，`UpperMoQty` 变 0，`SoDId` 从空串变 NULL，`EndDemDate` 取 `DStartDemDate`。`UpperMoQty` 不是现行行数量，U8 客户端改数量时保留它。
- `MOrderLoad`（参数 `mocode`、`extbo` out）填出整张订单；数量按数量精度做银行家舍入，子件 `DChangeRate` 按件数精度。子件的 `DRemark`、`DProductType` 不在 Load 里；`ParentScrap`、`OpComponentId`、`QcFlag`、`UpperMoQty`、条码、需求跟踪等列实体里没有。
- 改行的开工 / 完工日期时，U8 客户端按工作日历 + 偏置期（`Offset`）重算子件需求日期；`MOrderUpdate` 重建子件时照抄 Load 带出的旧 `DStartDemDate`。
- 子件数量算法：`f = 1 + CompScrap/100`，变动用量（`FVFlag=1`）且非返工订单（`MoClass≠2`）再 `/(1 − ParentScrap/100)`；单位用量 `BaseQtyN / BaseQtyD × f`，有 `AuxBaseQtyN` 和换算率时 `AuxBaseQtyN / BaseQtyD × f × 换算率`；变动用量 × 行数量，固定用量不乘；件数 = 数量 / 换算率。U8 展开时存的 `BaseQtyN` 是 `AuxBaseQtyN × 换算率` 按数量小数位四舍五入的值，所以主、辅两条算法可能差一个最小单位。
- `U8ApiComBroker.Disconnect` 只释放参数集，实体还在，可用 `SetExtBoEntity(名, 实体)` 交给新连接；同一个 broker 断开后再连报「ConnectionString 属性尚未初始化」，要新建 env 和 broker。

桥的做法（`MoUpdate*`）：

- 请求：开工日期不开放（400，原因见上面的需求日期）；有产出品子件（`ByproductFlag=1`）的行不能改完工日期（409）。
- 闸门（不满足 409）：全部行 `Status` 1 / 2 / 3（4 已关闭：「请先打开再修改」）、非集合、`IsWFControlled=0`、`DeclaredQty=0`、没有产成品入库、未报检；除材料出库单 `rdrecords11` 外没有表按 `AllocateId` 引用这些子件（`MoUpdateRefs` 名单：领料申请、调拨 `TransVouchs`、替代料、计划、序列号明细 `ST_SNDetail_MaOut.impoids`、历史表等）；同一行上（行号、存货）不重复；U8 重建时丢失、桥也不写回的子件列必须是缺省（`MoUpdateCols.Guard`：母件损耗、偏置期、质检、成本在制关联、需求跟踪、工厂、各种累计量和标志、成本项、`MoallocateSubId`，以及 `EndDemDate = StartDemDate`），否则「子件有 U8 修改接口不能保留的设置（列名），不能修改」。主用量与辅用量 × 换算率只在舍入后相等时，主、辅两条算法都算，数量、件数都相同才改（`MoUpdateAlloc.CheckAux`）。
- 调用：一个 broker `MOrderLoad` 取实体（`GetExtBoEntity("extbo")`，行数为 0 再试 `GetResult("extbo")`），用索引器 `Item(i)` 逐行、逐子件对上调用前快照（子件按行号、存货、用量、仓库、自由项），对不上 409；只改请求改了的行字段；每个子件写回快照的 `DBaseQtyN` / `DBaseQtyD` / `DAuxBaseQtyN` / `DQty` / `DAuxQty`（改了数量的行写重算值）、`DChangeRate`、`DRemark`、`DProductType`；`Disconnect` 后新建 env / broker，`MOrderUpdate` + `SetExtBoEntity("extbo", 实体)` + `InvokeApi`。不包 `CoTrans`，同一时刻只连着一个。
- 回写：调用前把每个子件的旧 `AllocateId` 和 `OpComponentId`、`VirOpComponentIds`、`cSubSysBarCode`、`SoDId`、`UpperMoQty` 写进审计事件 `mo_update_snapshot`。调用后在新连接上回读：调用失败且与修改前完全相同 → 503（IPC）/ 409；否则 U8 已提交，`MoUpdateRestore` 在自己的事务里（`UPDLOCK`）确认子件确实重插过，按（行、行号、存货）一对一写回上述原值（目标列已不是 U8 重建后的缺省、也不等于快照值时不覆盖），再完整核对 `MoUpdateCols` 名单和数量目标；任何失败回滚并 504，据 `mo_update_snapshot` 手工恢复。
- 未验证：Load 的数量精度是否就是 `iStrsQuanDecDgt`；主、辅用量严格相等时辅计量子件改数量的写入；小数位多于件数精度的子件换算率写回。

#### 已审核、已领料的订单

`MOrderUpdate` 不查审核状态：已审核订单改数量后行 `Status` 仍为 3，子件全部重插（新 `AllocateId`）、按新行数量重算，已领量带到同一（行号、存货）的新子件；但材料出库行的 `iMPoIds` 仍指向旧 `AllocateId`，成了孤儿（之后删出库单时 U8 回退不了已领量）。U8 客户端的「变更」则直接修改已审核订单并同时改写出库行的子件关联。所以桥要自己改写出库行（`MoUpdateRemap`）：

- 放开 `Status=3`，回读按行上的 `Status`、`RelsUser`、`RelsDate` 给 `state.verified` / `verifier` / `verified_at`。
- 有改动时，任何已领料子件的改后需求少于已领数量都 409（不论这一行改没改数量），这种订单请在 U8 客户端变更。调用前抓取全部 `rdrecords11` 引用行（`AutoID`、出库单 `ID`、旧 `AllocateId`、子件的行 / 行号 / 存货、数量）写进审计事件 `mo_update_refs`；出库行存货与子件不同（替代料出库）409。
- U8 提交后、`MoUpdateRestore` 之前，在新连接上开事务：`UPDLOCK, HOLDLOCK` 读现有子件，按（`MoDId`、行号、存货）把每个有引用或已领料的旧子件对到恰好一行新子件；再 `UPDLOCK, HOLDLOCK` 重读指向全部旧 `AllocateId` 的出库行（含抓取之后并发新增的），存货与子件不同 504；逐行 `update rdrecords11 set iMPoIds=新 where AutoID=? and iMPoIds=旧`，核对每行都指向同一存货的新子件、旧 id 不再被引用、新子件 `IssQty` 等于旧值（有并发领料的不比）。通过才提交，对照写进审计事件 `mo_update_remap`；失败回滚并 504 `outcome_unknown`，消息指向 `mo_update_snapshot`、`mo_update_refs`、`mo_update_remap`。改写失败仍照常执行 `MoUpdateRestore`，两步都失败时 504 消息合并。
- 产成品入库按行 `MoDId` 引用（行不重建），仍然拒绝。
- 未验证：已审核订单改完工日期；U8 客户端变更时领料申请（`MaterialAppVouchs`）等其他引用怎样改写。

### 物料清单

U8 API 的行为（已在测试账套实测）。桥的实现在 `BomCom`、`BomCreate`、`BomEdit`、`BomState`、`BomRead`。

- **登录与连接**：登录子系统 `BO`（卡片 `U870_TRANS_BO11`，`IB_AppType` 是 `BOM`）。env 与 broker 同生产订单，`Connect("U8API/BOM/<动作>", env)`。API 自己提交，不包 `CoTrans`。
- **新增** `BomAdd`：`Connect` 之后 `ext = GetExtBoEntity("extbo")`、`head = ext.NewItem()`，表头 `BomType`（1）、`InvCode`、`Version`（整数，该母件没用过）、`VersionDesc`、`VersionEffDate`（`yyyy-MM-dd`，不能与其他版本相同）、`ParentScrap`（0）；`head.GetSubEntity("Bom_Component")` 每行 `NewItem` 后填 `DSortSeq`（10、20…）、`DOpSeq`（`"0000"`）、`DInvCode`、`DBaseQtyN`、`DBaseQtyD`、`DCompScrap`（0）、`DWIPType`（3）、`DWhCode`（可省）、`DEffBegDate`（= 版本生效日期）、`DEffEndDate`（`2099-12-31`），**以及**标志 `DFVFlag`（1 变动）、`DOffset`（0）、`DPlanRate`（100）、`DByproductFlag`（0）、`DAccuCostFlag`（1）、`DOptionalFlag`（0）、`DMutexRule`（2）、`DProductType`（1）、`DCompScrapFlag`（0，U8 不读）、`DCostWIPRel`（0）。缺标志时 `SaveBOM` 抛 `BusinessRowItem.CheckStructureIntegrity`，不说是哪个字段。新 `bom_bom` 行 `Status=1`（按 `mom_parameter.BomDefaultStatus`），`bom_parent.ParentId` 是母件的 `bas_part.PartId`，子件在 `bom_opcomponent` / `bom_opcomponentopt`。`BomId` 不写回实体，要在新连接上按（`ParentId`、`Version`、`BomType=1`）查（它是 `UA_Identity_Ex` 计数加 10 亿，不是 IDENTITY）。远期生效日期、6 位小数的用量分子和损耗率都原样写入。
- **审核、弃审、删除**：`BomAuditing`、`BomUnauditing`、`BomDelete`，参数 `("partid", bas_part.PartId)`（整数）、`("bomtype", 1)`、`("versionoridencode", "40")`（版本号的**字符串**，不是 `BomId`）。审核后 `Status=3`，弃审回 1。已审核的删除失败（「该BOM处于审核状态，不允许被删除…」）；`Status=1` 的删除删掉 `bom_bom`、`bom_parent`、`bom_opcomponent`。删除不查生产订单：`mom_orderdetail.BomId` 会悬空。审核时 U8 顺延同一母件其他版本的失效日期，弃审按删除版本的规则恢复前一版本的失效日期；弃审不查生产订单是否在用。
- **修改** `BomUpdate`：表头送 `BomType`、`InvCode`、`Version` 和 `UpdateByDiff=true`，行按 `DSortSeq` 对上：送了的行更新，**没送的行删除**（不带 `UpdateByDiff` 时全部重建）。API 不看状态。差异更新时：`DInvCode` 与自由项总是重取物料（带自由项的子件会被换掉）；`DOpSeq`、日期、标志、`DCompScrap`、`DPlanRate` 等只在送了时写；`DAuxUnitCode`、`DDeptCode`、`DWhCode`、`DRemark` 只在非空串时写（**清不掉**）；表体自定义项 `DDefine_22`…`37` 只写送了的；改主用量不重算辅用量；没送的替代料（`Bom_ComponentSubs`）、定位符（`Bom_ComponentLoc`）被删；分段损耗不处理。表头 `VersionDesc`、`VersionEffDate`、`ParentScrap`、自定义项只在送了时写；只改 `VersionEffDate` 时各行子件生效日期跟到新日期。
- **读取**：`BomLoad(partid, bomtype, versionoridencode)` 后 `ext.Serialize()` 给出小写属性（`dfvflag`、`dplanrate`、`dwhcode`、`deffbegdate`…）。桥直接查表（`bom_bom`、`bom_parent`、`bom_opcomponent`、`bom_opcomponentopt`），不调 `BomLoad`。
- **默认仓库**：新子件行不送 `DWhCode` 时 U8 填存货的默认仓库（`Inventory.cDefWareHouse`），已有行不改。

桥的做法：

- 只做标准 BOM（`BomType=1`），替代 BOM 400。读取、列表走 SQL（登录 `SA`），写路由登录 `BO`。
- 新增前查库：母件存在、自制（`bSelf`）、允许 BOM 母件（`bBomMain`）、未停用、有不带自由项的 `bas_part` 行；子件存在、允许 BOM 子件（`bBomSub`）、未停用、有 `bas_part` 行、不是母件本身；仓库存在且未停用；版本号不重复（省略时取最大版本加 `VersionIncrement`）；生效日期不与其他版本重复。新行先按默认仓库规则补仓库。
- 新增后在新连接上按（母件、版本、主 BOM）找：U8 返回成功时认创建时间不早于调用前数据库时间的那一行；调用中途异常或 IPC 错误时另要 `CreateUser` 是本操作员、行号、子件、用量分子逐行相同。有这个版本却认不了 504；一行都没有时 IPC 错误 503、其他 504。
- 修改：先读出整张清单，把修改叠到现有全部行和标志上，以 `UpdateByDiff=true` 送全部行（每行都送起止日期）。只收 `Status=1`、`IsWFControlled` 不是 1、没有替代料（`bom_opcomponentsub`）、定位符（`bom_opcomponentloc`）、分段损耗（`bom_opcomponentscrap`）、子件自由项的清单；辅计量行不能改用量。改后在新连接上逐项比对表头版本说明、生效日期、母件损耗率和每行行号、工序、子件、用量、损耗率、供应类型、仓库、备注、起止日期、全部标志（`DCompScrapFlag` 除外）；IPC 错误时只有回读已是全部目标状态才按成功。
- 删除只收 `Status=1`，并查 `mom_orderdetail`、`OM_MODetails`、`AssemVouch`（组装、拆卸、形态转换）、`MatchVouch` / `MatchVouchs.Bomid`（配比出库）、`TransVouch`（调拨）的 `BomId`，有就 409 并说明是哪类单据（历史表不查）。审核只收 1，弃审只收 3。
- 功能权限取卡片 `BO_Voucher_BO01001` 的按钮（UFMeta `AA_FormButtonAuths`）：查询 `BO01001Q`、增加 `BO01001N`、修改 `BO01001M`、删除 `BO01001D`、审核 `BO01001A`、弃审 `BO01001UA`；数据权限按母件存货。
- 未验证：`CreateUser` 是否是操作员编码；`ModifyBOM` 是否要求表头 `VersionDesc` / `ParentScrap`（桥总是送）；启用审批流时 `BomAdd` 后的审核走向。

## 10. 总账

- 表一律用 `GL_accvouch` 并显式带 `iyear`，不要用视图 `gl_v_accvouch`。会计年度取登录日期的年份。
- 新增和修改走 EAI 凭证导入 `U8PzInsert.clsPZInsert`：`ToEAICon` 设为请求连接，`Transact(xml, login)`（by-ref `{1}`）返回应答 XML。`enter` 写操作员姓名，出纳、签字、审核、记账、冲销标志留空。
- 新增 `proc="add"`，`renewproofno="y"`（不是 `"true"`），凭证号留空，U8 按（年、期间、凭证类别）编号。应答 `succeed="0"` 为成功，否则 `dsc` 是原文。
- 修改 `proc="edit"`，`renewproofno="false"`，带原凭证号。这是整张替换：U8 删了再插，内部主键和时间戳会变。报文里调用方不能填的列（备注、外部单据类型和号、业务员等）要从原凭证照抄。
- 导入器留下 `GL_CashTable.csign` 为 NULL（U8 界面会填），导入后在事务里补：`UPDATE GL_CashTable SET csign=? WHERE … AND csign IS NULL`。
- 导入器自己提交，外层事务管不到；它还会改 `UFSystem` 里的一行开放接口令牌记录，这一行不在账套库里。
- 作废（`iflag=1`）、取消作废（`iflag` 置 NULL）、审核（写 `ccheck`、`daudit_date`）、出纳签字（`ccashier`）、删除（删 `GL_CashTable`、`GL_CodeRemark`、`GL_accvouch` 的行，不重排号）与 U8 界面执行的 SQL 一致（实测核对）。门槛：未记账（`ibook=0`）、期间未结账（`GL_mend.bflag=0`）、`GL_mvcontrol` 里没有这张凭证的锁、只动总账自己的凭证时看 `coutsysname`。
- 功能权限在 `UFSYSTEM..UA_HoldAuth`（本人或 `UA_Role` 里的角色，或 `admin`）。业务授权挂在建账年度（`UFSYSTEM..UA_Account.iYear`）下，新年度的库里通常只有账套主管的 `admin` 行；桥按请求年度或建账年度认授权；账套主管只看不晚于请求年度、最近一个有 `admin` 行的年度；不看凭证的会计年度，也不看调用方给的登录日期。`GL0201` 填制、`GL0202` 整理、`GL0203` 出纳签字、`GL0204` 审核。
- 数据权限的「用户」对象（`user`，按制单人控制）：打开开关、`AA_HoldAuth` 里没有一条授权时，U8 照样让别的操作员查看、审核他人制单的凭证。所以这个对象不限制查看他人单据，桥的读取不按它过滤（`PermObj.NotForRead`）。
- 总账选项 `bProofModify`、`bAllowSameCheck`、`bAllowUnCheckOther`、`bProofSign`、`bMakShtSort`、`bUseYSKM` / `bUseYFKM` 照 U8 的规则执行。

### 记账

- U8 界面的「记账」经本机 FAG HTTP 池（`U8FAGHttpPool.exe`，`u8api/fag/balance/*`）调用 `<u8Home>\LZW` 的 `UFIDA.U8.GL.Rules.BalanceRule` → `UFIDA.U8.GL.Dal.BalanceDao`。桥在进程里直接用这两个程序集（AnyCPU、net4.8），另需 `LServer\UFIDA.U8.U8HttpPoolContext.dll`、`Framework\UFSoft.U8.Framework.LoginContext.dll`，其余依赖只在记账调用期间按短名在 `LZW`、`LServer`、`Framework`、`U8Framework`、`Interop`、安装目录下解析。全部反射晚绑定（`GlPostNet`），缺了 503。
- 上下文：在执行线程上设 `HttpLoginContext.UserData`（`[ThreadStatic]`）：`ConnString4Dn` = 登录对象的 `UfDbName` 去掉 `Provider`（要用 U8 自己的库账号，记账跨库读 `UFSystem`），`UserName` = 操作员姓名（写进 `cbook`），`AccID` = 账套号，`operDate` = 登录日期；`finally` 里清掉。年度是方法参数。
- 顺序（同 U8 界面）：`BalanceDao.VouchPostGather(期间, ccash, tcond, imaster, 年度)`（`ccash` = `bProofSign`、`imaster` = `bMasterSign`；`tcond` 形如 `(isignseq=1 and ino_id in (1,2))`，主管签字开着时列名带 `A.`）先删本年度的 `GL_mpostcond1` 再由 `GL_P_JZA` 填入合格的凭证（不合格的静默跳过），返回的 `Table3` 是范围内未记账的凭证；然后 `BalanceRule.VouchPostAll(期间, 年度)` 记 `GL_accsum` / `GL_accass` / `GL_AccMultiAss` 并回写 `GL_accvouch`、`Gl_mpostcond`。`VouchPostAll` 自己不查任何状态，只记 `GL_mpostcond1` 里的凭证。
- 事务：U8 的 DAL 每条语句新开连接，`VouchPostAll` 里用缺省构造的 `TransactionScope`（单独运行时 Serializable、60 秒），它加入外层事务时不比对隔离级别、不另起计时器。桥在外面包 ReadCommitted、超时 5 分钟的 `TransactionScope`，桥自己的读写用同一个规范化连接串（`Min Pool Size=1`），同一时刻只开一条，不升级 MSDTC；Complete 之前核对 `DistributedIdentifier` 为空，不是就回滚、503；成功响应带 `local_txn: true`。事务第一条语句给 `GL_mpostcond1` 加表级更新锁（`UPDLOCK, HOLDLOCK, TABLOCK`），快照读 `GL_accvouch` 带 `UPDLOCK, HOLDLOCK`，桥的记账一律持锁键 `gl:post`（`GL_Acc_Temp*` 是账套库里的共用表）。`GL_P_JZA` 在 `@ccash=1` 或 `@imaster=1` 时对实例共用的 `tempdb..GL_jztmp` 做 DDL，锁持有到提交：同一实例上所有账套的记账都会等桥提交。
- 错误：死锁、锁超时、执行超时、事务中止都已回滚，503 可重试；U8 组件抛的非 SQL 异常 409 `u8_rejected`；其他 SQL 错误 500（`GlPostErr`）。
- 范围表：`GL_mpostcond1` U8 记完不清；里面有未记账凭证说明有人汇总了范围还没记账或已放弃，U8 自己的汇总直接覆盖，桥也覆盖。`VouchPostAll` 先把本年度范围复制到 `Gl_mpostcond`，桥随后在同一事务里清掉 `GL_mpostcond1` 本年度的范围，所以 U8「恢复记账前状态 → 最近一次」照常可用。
- 与 U8 客户端交错（未实测）：U8 用户在桥加锁前已在记账向导里汇总、桥记完后再点「记账」时，U8 在空范围上什么都不记（可能照样报成功）并清空 `Gl_mpostcond` 本年度，「恢复记账前状态」就撤销不了桥这次的记账。应让对方重新汇总再记账。
- U8 缺陷（实测）：记账回写 `update gl_accvouch set ibook=1, cbook=N'<UserName>' from gl_accvouch INNER JOIN GL_mpostcond1 ON (iperiod, isignseq, ino_id)` 不带年度：别的年度期间、类别、凭证号相同的凭证会被标成本操作员记账。桥在同一事务里快照这个 `UPDATE` 能碰到的全部非本次凭证行（`i_id`、`iyear`、`ibook`、`cbook`），调用后按 `i_id` 改回并核对。姓名直接拼进 SQL，带单引号的操作员桥调用前拒绝。
- 年度首张凭证（本年没有 `ibook=1`）：`bAllowKeepAccouts` 为假时先期初对账 `BalanceRule.QcCheck(0, 年度, -1)`（界面只看 `GLUpper_Lower`、`GLSum_Ass`、`GLSum_MultiAss`、`GLAss_Vouch`；它清写 `GL_merror`、更新 `GL_mend` 第 0 期的 `bpri_check`；这条路径未验证，桥内失败时 409），再期初试算 `BalanceDao.GetTrialBalance(0, 年度)`：一级科目第 1 期 `mb` 借正贷负合计为 0 才平；年中建账（建账年度等于操作年度、建账期间大于 1）时用 `GetTrialBalance(1, 年度)`，即第 1 期的 `me`。
- 作废凭证（`ccheck is null and iflag=1`）也被记账，只打标志，不计入发生额。出纳凭证按 `(code.bbank ^ code.bcash)=1` 判断。桥的预检与此一致。

### 取消记账（只限测试账套）

- U8 的「恢复记账前状态」没有可调用的组件，桥按记账的公式反做：范围取 `Gl_mpostcond` 本年度（记账时从 `GL_mpostcond1` 复制过去的最近一次范围），按记账的联表和 SET 公式把发生额取负再做一遍（`GL_accsum` 本期和后续期间的余额链、`GL_accass`、`GL_AccMultiAss`；按币种分行看总账选项 `IsUseMultiCurrency`，本位币名称取 `UFSYSTEM..UA_Account.cCurName`），凭证改回 `ibook=0`、`cbook` 为空，最后删掉 `Gl_mpostcond` 本年度。锁的顺序同记账（`GL_mpostcond1` 表锁 → 范围 → `GL_mend` → 行），每条语句都带年度。
- 记账时新插的余额行只改回零、不删（U8 不保留记账前的状态，分不出哪些行是那次新插的）。
- 提交前自检：科目总账发生额与已记账凭证重算一致、余额首尾相接、辅助账发生额一致，不符回滚。
- 功能 id 暂按记账 `GL0208`；U8 的恢复记账通常只给账套主管，授权目录里没有单独的 id。

### 红字冲销

- U8 做的红字冲销凭证：摘要前缀 `[冲销yyyy.MM.dd 类别-NNNN号凭证]`（原凭证的制单日期、类别字、补零的凭证号）加原摘要；红字凭证的 `cblueoutno_id` = 原凭证的外部业务号 `coutno_id`，自己的 `coutno_id` 从 `Ap_Proc_CancelNo` 的 `PZ` / `GL` 计数取；辅助项、结算方式、票号、币种汇率、原始单据、业务员照抄，金额、原币、数量取负，附单据数照抄，类别同原凭证。常见用法是跨年冲销（上年 12 月的凭证在本年 1 月冲销）。U8 允许同一张凭证冲销多次、部分冲销或冲销后再改，桥只做整张、一次。
- 凭证导入（`U8PzInsert`）生成的凭证没有 `coutno_id`；桥冲销时先给原凭证补一个（原凭证所在期间已结账时照样补，只是关联标识）。红字凭证的金额、原币、数量在导入报文里写负数（单价写正数），只有冲销这条路放开负数。导入组件自己提交，所以取号、写关联放在保存之后的一个事务里，U8 拒绝保存时什么都不留。

### 期间损益结转、自定义转账（只限测试账套）

- 转账定义只在 `GL_bautotran`：`itype=30` 期间损益，每个转账序号 `ctran_id` 两行（`inid=1` 损益科目、第 2 行本年利润科目），没有公式，`bd_c` 不用（方向按余额）；`itype=10` 自定义转账，`ccode` 行科目、`bd_c` 1 借 0 贷、`cformula` 金额公式（另有数量、外币公式 `cformula_s` / `cformula_f`）、`cdigest` 摘要、`ctext` 说明、`csign` 凭证类别；`itype=50` 的汇兑损益遗留空壳桥不碰。定义上的运行列（`RefVouch*`、`iCount`、`bAuto`、`operater`）U8 不维护。
- 公式：桥支持 `QM(科目,月)`、`QM(科目,月,,部门)` 乘常数和 `CE()`，`QM` 的科目可以不是本行科目。带方向的按年取数 `QM(…,年,借|贷)` 的口径无法核对，桥不支持（给 `tran_id` 时 400，不给时跳过该定义）；`FS`、`JE`、`QC`、`LFS` 不支持。余额在反方向的科目、带多个辅助项参数的 `QM` 未验证。
- U8 生成的结转凭证：`coutsign` 为 `期间损益` / `自定义转账`（摘要改了也不变），`coutno_id` 是 `GL` 加 13 位（`Ap_CancelNo` 的 `PZ` / `GL` 计数），`idoc=-1`，`coutsysname` 为空（作废、删除按总账手工凭证处理），`ioutyear` 为空、`ioutperiod` 是期间、`doutbilldate` 是凭证日期（月末），`bvouchAddordele=0`，`coutid` / `coutbillsign` 为空。期间损益每月两张：收入（借损益科目、贷本年利润在最后一行）和费用（借本年利润在第一行、贷损益科目）；同一科目按辅助项分行。凭证上推不回转账序号，「本期已生成」只能按期间、`coutsign`、摘要认。
- 结转取已记账余额（`GL_accsum` / `GL_accass` 的 `me`），生成前本期凭证应已全部记账。桥导入时不带外部来源（报文同 `create`），保存后在事务里补上面这些列再回读。`exclude_existing` 去掉已生成的结转凭证后重算，可与 U8 已生成的凭证逐行比对。

### 损益结转凭证的识别

- 结转之后损益科目在 `GL_accsum` 上本期借贷相等、`me=0`，利润表要从凭证取发生额并排除结转凭证。
- U8 自动转账生成的期间损益结转凭证 `coutsign = N'期间损益'`（系统写入，摘要改了也不变）；手工做的结转按「凭证里有本年利润科目、其余分录全是损益类科目」识别，损益类看科目档案 `code.cclass`（取值有资产、负债、权益、成本、损益）。这两条合起来与按摘要「期间损益结转」筛出的凭证一致，另外还能排除摘要是结转、却只在两个损益科目之间互转（本年利润分录为 0 被省掉）的凭证。「结转销售成本」「结转制造费用」「汇兑损益」是经营业务，不排除。

### 期初与附件

- 期初记账没有账套选项，标志是 `GL_mend` 的第 0 期（`iperiod=0`）：`bflag`（总账）、`bflag_ST`、`bflag_AR`、`bflag_AP`、`bflag_IA` 等。启用年度的这一行在期初记账后为 1；年结会把下一年第 0 期的总账、存货核算标志置 1，库存和往来不置。模块启用日期在 `AccInformation`（`cSysID` 为模块，`cName` 为 `d<模块>StartDate`，值是 `yyyy-MM-dd` 字符串）。
- 库存期初是单据类型 34：`rdrecord34` / `rdrecords34`（卡片 `0319`，`KCQC`），`bIsSTQc=1`，日期是启用日前一天。采购入库单 `bpufirst=1`（采购期初入库）、发货单 `bFirst=1`（销售期初发货）都不是库存期初。
- 应收应付期初是 `Ap_Vouch` 上 `bStartFlag=1` 的应收单 / 应付单，只有表头（`Ap_Vouchs` 没有行），金额在表头 `iAmount`，`bd_c` 1 借 0 贷，审核后写 `Ar_Detail` / `Ap_Detail`，不生成凭证。
- 总账期初余额是 `GL_accsum` / `GL_accass` 在期初期间（启用年度为启用月份，之后各年为 1 月）的 `mb`，方向看 `cbegind_c`；没有第 0 期的行。年中启用时启用前各期是 `md=mc=0` 的空壳。期初试算只加末级科目。
- 凭证电子附件在 `GL_AccAttachs`（`Inid`、`Iperiod`、`isignseq`、`ino_id`、`iyear`、`CClientFileName`、`CServerFileName`、`SubmitTime`、`csource`；触发器补 `iyear`），文件在 U8 文件服务器。`GL_accvouch.idoc` 只是附单据数。
- 单据卡片的附件在通用表 `VoucherAccessories`（`VoucherTypeID`、`VoucherID`、`FileID`、`FileName`、`Memo`、`FileContent`），按 `VoucherTypeID='<卡片号>' and VoucherID='<主键>'` 读写；主键列看 `vouchers.VchTblPrimarykeyNames`（应收应付单是 `cLink`，不是 `Auto_ID`）。

## 11. 基础档案

- `U8SrvTrans.IClsCommon.Transact(xml, login)`，by-ref `{1}`，应答 `<ufinterface roottag="return"><item … succeed="0" dsc="ok" u8key="…"/></ufinterface>`。它自己提交。
- 信封 `<ufinterface roottag='根' proc='add|diffedit|delete' …>`。根标签：`customer`、`vendor`、`inventory`、`department`、`v_aa_hr_hi_person`（人员）、`warehouse`、`customerclass`、`vendorclass`、`inventoryclass`。存货还要 `<body><entry><invcode>编码</invcode></entry></body>`。
- 字段名是 EAI 标签，标签到列的对照在 U8 安装目录的 `EAI\XML\RsXml\<文件>.xml`。桥只读这些文件并缓存。
- 修改用 `proc='diffedit'`，但要发整条记录：当前行所有有对应列、非空、可写的标签打底，再覆盖调用方字段。U8 在 diffedit 时也按档案设置检查全部必输项，只发改动的标签会被拒绝。
- 新增缺省：客户管理类型、供应商至少一种属性（采购/委外/服务）、人员证件类型 `rIDType=0`。人员删除也要带当前的 `rIDType`。
- 读取和列表直接查表：人员合并 `hr_hi_person` 和 `Person`，存货合并 `Inventory` 和 `Inventory_Sub`。
- 已被单据使用的档案 U8 自己拒绝删除（「…已经在…中使用！不可删除！」）。
- EAI 的路由：`EAI\XML\Operation\Dir.xml` 里 `in="y"` 的根标签可导入，`Distribute.xml` 给出分发目标；下面各类档案注明目标。U8 自己的分发器 `U8Distribute` 对分发目标一律晚绑定 `Transact`。

### 货位、计量单位、自定义项、客户存货对照

- 货位（根标签 `position`，`PositionXmlRs.xml`）、计量单位（`unit`，`UnitXmlRs.xml`）、自定义项档案（`define`，`DefineXmlRs.xml`，表 `UserDefine`）、客户存货对照（`cusinvcontrapose`，`CusInvContraposeXmlRs.xml`）都分发到 `U8SrvTrans.IclsCommon`。`UserdefXmlRs.xml`（根 `userdef`）是自定义项设置，不是档案值。
- 自定义项档案和客户存货对照没有 `code` 标签，编码由 `id` + `value`、`ccuscode` + `cinvcode` 给出，桥按这两个标签发。U8 不支持修改这两类（`edit` 回「自定义项档案不提供修改功能！」「对照表不提供修改功能！」，`diffedit` 回「该修改的记录可能被删除！」「系统忙，稍后再试！」），桥只做新增和删除（修改 400 `bad_request`）。对照表唯一索引是（`cCusCode`、`cInvCode`、`cCusInvCode`），而 API 的编码只有（客户、存货）两段：每一对只能管一条，已有多条的在 U8 客户端维护（`limitations.md`「基础档案」）。
- 货位编码方案在 `GradeDef_Base` 的 `positionclass`，级次 `grade`（`iPosGrade`）、末级 `end_flag`（`bPosEnd`）；新增下级货位后 U8 把上级 `bPosEnd` 改成 0，删掉下级后改回 1。计量单位组 `ComputationGroup.iGroupType`：0 无换算（每个单位 `bMainUnit=1`、`iChangRate` 为空，`iNumber` 从 0 递增），1 固定换算（主计量单位换算率 1、`iNumber` 为空，辅计量单位 `iNumber` 从 1 递增）。自定义项设置在 `UserDef_Base`（视图 `UserDef` 按语言过滤），`bArchive=1` 才有档案值；使用长度按字符计（`cValue` 是 nvarchar）。
- 未验证：计量单位的主计量单位标志、序号是否由 EAI 自己补（桥先补）；U8 是否允许改已被存货使用的计量单位的换算率（桥先拒绝）。
- 功能 id：自定义项 `AS025A` / `AS025` / `AS025D`，客户存货对照 `AS1204A` / `AS1204E` / `AS1204D`，计量单位 `AS032M`；货位「货位档案查询」`AS030Q`、「货位档案编辑」`AS030`（读取收两者之一，写入查 `AS030`）；本单位开户银行同理 `AS013Q` / `AS013`。

### 计量单位组、结算方式、收发类别等

- 计量单位组（`unitgroup`）、结算方式（`balancetype`）、收发类别（`receivesendtype`）、采购类型（`purchasetype`）、销售类型（`saletype`）、地区分类（`districtclass`）、银行档案（`aa_bank`）都分发到 `U8SrvTrans.IclsCommon`，定位标签只有 `code`，桥发 `diffedit`，增、改、删已在测试账套实测。
- 编码方案（`GradeDef_Base`，`iYear=0`）：结算方式 `settlestyle`（`code_rank` → `iSSGrade`，`end_rank_flag` → `bSSEnd`）、收发类别 `rd_style`（`sort` → `iRdGrade`，`end_flag` → `bRdEnd`）、地区分类 `districtclass`（`sort` → `iDCGrade`，`endflag` → `bDCEnd`）。收发标志 `rsflag` → `bRdFlag`（1 收、0 发）。
- 非空列 `SettleStyle.bSSFlag`、`iSSBillType`，`PurchaseType.bDefault`、`bPFDefault`，`SaleType.bDefault`，`ComputationGroup.iGroupType` 由桥给缺省值（`bPTMPS_MRP`、`bSTMPS_MRP`、`bDefaultGroup`、`AA_Bank.bImport` 不在 RsXml 里，交给 U8）。
- 只能有一个无换算组，再建 U8 回「计量单位分组最多只能有一个无换算单位组！」，桥调用前查、409。档案设置把入库 / 出库类别设为必输时，采购类型、销售类型缺 `rstype_code` 时 U8 回「入库类别不可为空！」「出库类别不可为空！」，原文带回（加「（U8 档案设置为必输）」）。

### 币种、凭证类别、会计科目、汇率

- 币种（`currency`）分发到 `U8PzInsert.icurrency`，凭证类别（`dsign`）分发到 `U8PzInsert.IDsign`。只调 `Transact(xml, login)`（by-ref `{1}`），不设 `ToEAICon`（这两个类没有该成员，报 `DISP_E_UNKNOWNNAME`）；按 `GL` 登录新增成功。`diffedit`、`delete` 回「暂不提供此项功能」，所以桥（`ArcGl`）只经 EAI 新增，修改、删除走受控 SQL（`ArcCurrencySql`、`ArcSignSql`）。
- 币种：`code` → `cexch_code`（币种符号），`name` → `cexch_name`，`id` → 自动编号 `i_id`；`iotherused` -1 本位币，非 0 表示已被其他系统使用。
- 凭证类别：`type` → `csign`（最长 2）、`type_name` → `ctext`（唯一）、`order_code` → `isignseq`（改排序号要同时改凭证表和收付款结算表）、`other_use_flag` → `iotherused`；另有 `itype`（限制类型）、`cOtherName`、`iAdjustFlag`，限制科目在 `dsigns`。`csign` 还被 `GL_VouchDraft`、`GL_bfreq`、`GL_bautotran`、`GL_CashTable`、`Ap_Detail`、`Ar_Detail`、`fa_ZWVouchers` 等引用。
- 会计科目（`code`，分发到 `U8PzInsert.ICode`）：`type` → `cclass`、`grade` → `igrade`、`prop` → `bproperty`、`end_item_flag` → `bend`、`ctrled_acc` → `cother`。科目表和编码方案都按年度（`GradeDef_Base` 的 `KEYWORD='code'` 每年一行，不是 `AccInformation` 的 `AA.cGradeLevel`）。给末级科目加下级时 U8 把上级改成非末级并把余额挪到下级。`ICode.Transact` 新增报「未设置对象变量或 With block 变量」，报文也没有年度，科目只读。
- 功能权限：外币设置 Add / Delete 是 `AS028M`；凭证类别 Add / Modify / Delete 都是 `AS026`；会计科目没有表单按钮。
- 汇率 `currencyrate` 没有 RsXml（`Template\CurrencyRate.xml`：`id`、`name`、`period`、`type`（1 浮动、2 固定、3 调整）、`date`、`rate`），没有年度。经官方分发器 `U8Distribute.iDistribute.ProcessEx(xml, login)`（by-ref `{1}`，按 `AS` 或 `GL` 登录均可）`add` 成功（「导入完成！」），`exch` 写登录年度、`cdate` 是期间号（固定、调整汇率）；同一键再导入也回「导入完成！」但不改 `nflat`；`edit` 回「暂不提供此项功能」。桥（`ArcExchWrite`）只在键不存在时经分发器新增，修改、删除走受控 SQL（`ArcExchSql`）。
- 行业（`TradeClass`）、客户收货地址（`CusDeliverAdd`）没有 EAI 根标签，只读。

### 银行账户、联系人

- 表：`CustomerBank` / `VendorBank`（主键（客户或供应商编码, `cAccountNum`），`bDefault` 非空；`cBank` 所属银行编码、`cBranch` 开户银行名称）；`Crm_Contact` / `Ven_Contact`（主键 `OID`；`Crm_Contact.cContactCode` 全表唯一，`Ven_Contact` 没有唯一索引）。客户档案 RsXml 的 `bank_open`、`bank_acc_number`、`contact` 只对应 `Customer` 上的列。
- 银行账户：EAI `diffedit` 附账户子节点时 U8 按整条客户档案校验（如「所属行业是默认值，不可为空！」），供应商回读不符，所以桥走受控 SQL，规则同 U8 档案服务：设默认账户时 `update CustomerBank set bDefault=0 where cCusCode=… and cAccountNum<>…`，再 `update Customer set cCusBank=…, cCusAccount=…, cCusBankCode=…`（取 `cBranch`、`cBank`）；`cBank` 要在 `AA_Bank` 里，账号不能为空、同一客户不重复。
- 联系人 EAI：`customerlinker` 分发到 `U8CRMEAINew.clsCRMEAI`，`vendorcontact` 分发到 `VencontactSrvEAI.clsCRMEAI`（`proc` 为 `add`、`edit`、`Delete`）。标签：`code` → `cContactCode`、`name` → `cContactName`、`of_customer` / `of_vendor`、`title` → `cAppellation`、`sex` → `bcSexID`、`position` → `bcDutyID`、`marriage` → `bcMarriageID`、`favorite` → `bcTasteID`、`be_main_linker` → `bMajor`、`self_define1`… → `cConDefine1`…。性别、婚姻在报文里是文字，表里存 `Crm_BaseCode_Base.ID`（男 / 女 / 不详 6 / 7 / 8；已婚 / 未婚 / 不详 / 离异 9 / 10 / 11 / 250）。`Ven_Contact.bcDutyID`、`bcTasteID` 是整数列，`Crm_Contact` 是文字列。
- 客户联系人按 `Transact(xml, login)`（by-ref `{1}`）可用：`add` 不用报文里的编码，U8 自己编号；`edit` 未送的列保留。`Customer.cCusContactCode`、`Customer_ContraRef.cCusContactCode`、`Vendor.cVenContactCode` 是 `uniqueidentifier`，存联系人 `OID`（要经 `Crm_Contact` 按 `OID` 连）；销售订单、发货单、销售发票、报价单的 `ccuspersoncode`、销售支出单的 `ccontactcode` 是联系人编码。删除时 U8 是否另查 CRM 引用未验证，U8 拒绝时 409 带原文。
- 供应商联系人：直接调组件新增报「数据库中没有该字段(bcsexid)…」；经分发器 `ProcessEx` 发 `vendorcontact`（`code` 留空）新增成功，`u8key` 是 U8 编的编码（供应商编码加 8 位流水，如 `S90000100000001`）；`edit` / `delete` 回「供应商联系人不支持修改和删除导入」。桥（`ArcVenContact`）新增走分发器，修改、删除走受控 SQL（`ArcVenContactSql`）。
- 功能权限：供应商联系人 Add / Modify / Del / Filter 是 `AS020305` / `AS020301` / `AS020306` / `AS020302`；客户联系人「客户联系人管理」查询 `CS020202`（读取另收 `AS011Q` / `AS011`）、增加 `CS020204`、编辑 `CS020201`、删除 `CS020205`；客户银行账户没有自己的表单按钮。

### 原因码

- 根标签 `reason`，`ReasonXmlRs.xml`，表 `Reason`；`in="y" out="y"`，分发到 `U8SrvTrans.IclsCommon`，登录子系统 `AS`。标签 `code` → `cReasonCode`、`name` → `cReasonName`、`Reasontype` → `iReasontype`（tinyint，非空）、`ReasonMemo` → `cReasonMemo`（大小写照 RsXml）。长度：编码 10、名称 30、说明 240；所属类型 1 不良品原因、2 让步放行原因、3 采购退货原因、4 销售退货原因、5 变更原因、6 拖欠原因（有的账套另有预置 `Refund` 类型 15 退款退货）。
- `add` 建行；`edit` 带整条记录（桥 `ArcKind.EditProc`）；`delete` 只带 `<code>`；应答同 `IClsCommon`。`query` 忽略条件，不用。U8 桌面「原因码档案」没有功能 id，UFMeta 也没有它的表单按钮。
- 引用原因码的业务列：`QMREJECTVOUCHERS.CREASONCODE`、`QMREJECTVOUCHER.CRETURNREASONCODE`、`QMCHECKVOUCHER.CREASONCODE` / `CRETURNREASONCODE`、`QMINSPECTVOUCHERS.CRETURNREASONCODE`、`DispatchLists`、`SaleBillVouchs`、`SA_ReturnsApplyDetail`、`SA_SettleVouchs` 的 `cReasonCode`；生产、车间表的 `ReasonCode` 未核实。

### 固定资产卡片与设备台账

- 都经官方分发器 `U8Distribute.iDistribute.ProcessEx`，按 `AS` 登录。卡片 `capitalasserts`、变动单 `capitalvouchers`、设备 `eqdata` 分发到 `EAIFaCards.clsFaCards`、`EAIFaCards.clsFaVouchers`、`U8EQEAI.clsEQEAI`。报文是表头 + 表体 `entry`。
- 卡片的 `assetno` 对应 `fa_Cards.sAssetNum`（资产编号），不是卡片编号 `sCardNum`；卡片编号由 U8 按选项自动编（`bAutoFillNumber`、`iCardNumLen`）。变动单表 `fa_Vouchers` 只有 `sCardNum`。
- EAI 导入的是原始卡片：`startusedate` 要早于登录期间，新卡片 `iOptType=2`（`bNewAssetDepr=False` 时录入当月不计提折旧）。表体 `deptno` + `deptscale`（多部门看 `bMultiDept`）。
- 期间：`AccInformation` 的 FA `iLastPeriod`（当前期间）、`dWritableDate`（可写日期），结账标志 `GL_mend.bflag_FA`。桥在调用前按登录日期核对。
- 卡片 `proc=delete` 只带表头 `assetno`，是撤销本期新增，不是资产减少。
- 变动单类型（`vouchertype`）：1 原值增加、2 原值减少、3 部门转移、4 使用状况调整、5 累计折旧调整、6 使用年限调整、7 折旧方法调整、8 工作总量调整、9 净残值调整、10 类别调整、11 计提减值准备、12 转回减值准备、13 减值准备期初。`voucherdata` 对 1 / 2 / 11–13 是金额，对 4–10 是变动后的值，部门转移在表体。变动单导入不可用（EAI 没有 `capitalvouchers` 的样式表，回「未设置对象变量或 With block 变量」）：变动单、资产减少在 U8 客户端做，API 不提供。
- 设备台账 `EQ_EQData`（主键 `AutoID`，业务键 `cEQCode`，有 `ufts`）：EAI 只能新增；`cAssetNum` / `cCardNum` 可为空。
- 未验证：卡片 `proc=delete` 的实际行为；设备管理的功能权限 id；`eqdata` 的必输标签。

## 12. 审批流

审批数据全部在账套库里：

| 表 | 内容 |
| --- | --- |
| `Table_WorkFlowRelease` | 已发布的流程。`Status = 0` 为启用 |
| `Table_Task` | 待办。`cTK_State` 0 待办、2 已办、3 同节点他人已办、9 作废；`cTaskType` 1 审批、4 弃审后重审、5 退回后待重新提交 |
| `WF_ActiveFlow` | 流程实例。`FlagCode` 0 进行中、2 结束 |
| `WFAudit` | 历史。`Action` 0 提交、1 同意、2 不同意、5 撤销、6 退回、7 弃审、8 重新提交 |
| 单据表头 | `iVerifyStateNew`（0 未提交、1 审批中、2 通过、-1 不通过）、`IVERIFYSTATE`、`CVERIFIER`、`cCurrentAuditor` |
| `UserHrPersonContro` | 操作员与人员的对应。审批人配置的是人员，不是操作员 |

| 动作 | 调用 |
| --- | --- |
| 提交 | `UFLTMService.clsService` 开事务，`QMWorkFlowSrv.clsQMFinalVerifyPI.DoSubmit(biz, biz+".Submit", id, login, ufts, ref err)`，成功才提交 |
| 撤销提交 | 同一事务里 `UndoSubmit(id, biz, login, ufts, ref err, code)` |
| 同意 | `AuditServiceProxy.Audit2(keySet, 1, 0, 意见, token, ref result)` |
| 不同意并继续 | `Audit2(keySet, 2, 2, …)` |
| 退回提交人 | `Audit2(keySet, 2, 0, …)` |
| 弃审 | `Abandon2(keySet, 意见, 0, token, ref result)` |
| 退回后重新提交 | `SubmitResubmitMessage2(keySet + ReSubmit, token, ref err)` |

`keySet` 是 `<KeySet><Key name="VoucherId" value="…"/><Key name="VoucherType" value="QM04"/><Key name="VoucherCode" value="…"/></KeySet>`，`token` 是登录对象的 `userToken`。审批代理和事务服务自己开连接和事务，不要再包桥的 ADO 事务。

审批引擎在 .NET 线程池里给 U8 移动端推送消息，所需程序集（`YonYou.U8.MA.*` 等，在 `U8AuditWebSite\bin` 和 `U8AuditWebSite\bin\Query`）在非 U8 进程里解析不到会结束进程。桥缺省拦下不推送；`config.json` 的 `mobilePush` 为 `true` 时从这两个目录加载、照常推送（见 `architecture.md`）。组件日志在 U8 安装目录的 `Workflow\Logs`。

判断单据是否走审批流：`AuditBizObjects` 里要有该表的行；`Table_WorkFlowRelease` 有该业务对象 `Status = 0`、事件为 `<对象ID>.Submit` 的发布，或单据的 `iswfcontrolled` 为真，就是启用。查不到按失败处理，不当成「没有启用」。

### 审批流终审留下的孤儿任务

经桥做终审（最后一个节点的 `Audit2` 同意）或弃审时，U8 的 QM 终审插件在桥的进程里另开 QM 登录且从不注销：每次在 `UFSYSTEM..UA_TaskLog` 留下一到两行（`cSub_Id='QM'`、`cStation` 为本机），`ua_Task_Common` 可能也有对应行，在系统管理的任务列表里越积越多。点数按「工作站 × 模块」计，这些行不额外占加密点数，只是列表残留。

`config.json` 的 `cleanOrphanTasks` 为 `true` 时，桥在每次调到 U8 的审批流操作（提交、撤销提交、同意、不同意、退回、弃审、重新提交，含 U8 拒绝）后自动清理（`TaskOrphans.cs`）：

1. **串行**：审批流操作在任何 U8 调用（含 `IsFlowEnabled2`）之前先取进程内的一把锁，清理结束才放。排队最多 60 秒，且不超过请求期限（入队起 75 秒）减 30 秒；看门狗已判定进程不健康时不再等。等不到在调 U8 之前返回 503 `busy_timeout`（「审批流操作排队中，请稍后重试」），什么都没写，不占幂等键，可以重发。
2. **窗口**：调 U8 前后各取一次 SQL Server 的 `GETDATE()` 作窗口起止，调用前快照 `UA_TaskLog` 里 `cStation` 为本机名（`Environment.MachineName`，等于桥的 SQL 会话的 `HOST_NAME()`）的全部 `cTaskId`。快照失败（`orphan_tasks_failed`）、超过 2000 行（`orphan_tasks_skipped`，`snapshot_too_large`）或请求没有操作员、账套（`no_operator`）时本次不清理，锁照常持有。
3. **候选**：状态回读之后，`cStation` 为本机名、`cSub_Id` 在固定名单里（只有 `QM`）、`cTaskId` 不在快照里、`dInTime` 在窗口内的行。`UA_TaskLog` 没有操作员和账套列，所以再看 `ua_Task_Common`。
4. **删除**：在新连接上（`SET DEADLOCK_PRIORITY LOW`、`SET LOCK_TIMEOUT 2000`）开短事务，`UPDLOCK, ROWLOCK`（不加 `HOLDLOCK`）锁住候选在 `UA_TaskLog` 的全部行。同一任务号还有非 QM 的行时回滚（`orphan_tasks_skipped`，`shared_task`）；锁到的行数不等于候选数时回滚（`count_mismatch`）；`ua_Task_Common` 里有行而 `cUser_Id` / `cAcc_Id` 不是本次请求的操作员、账套的候选去掉（`foreign_owner`，`count` 为去掉的个数），没有行的保留；这些行超过 500 行时回滚（`owner_rows_too_many`）；候选多于 10 个时不删（`too_many`）。通过后先删 `ua_Task_Common`（`cStation` + `cTaskId`），再删 `UA_TaskLog`（`cStation` + `cSub_Id` + `cTaskId`），删后仍有残留就回滚。
5. **结果**：清理从不影响请求结果。失败审计 `orphan_tasks_failed`（带 SQL 错误原文），清掉了审计 `orphan_tasks_cleaned`（数量和子系统）并在成功响应里加 `orphan_tasks_cleaned`。`meta` 的 `features.clean_orphan_tasks` 显示开关的实际值。

缺省关闭：清理要写 `UFSystem`，而同一个 `cStation` 也被服务器上的管理员和 U8 服务使用，窗口内有人在服务器上以同一操作员、同一账套登录 QM 时他的任务行会被误删，所以只在确认没人这样用时打开。打开后审批流操作串行执行。配置了 `sql.json` 时，专用登录要能读写 `UFSYSTEM..UA_TaskLog` 和 `UFSYSTEM..ua_Task_Common`。`UA_TaskLog` 只列登记过的任务，UFNet 仍持有的点数租约可能不在里面，按它统计站点数会偏少。

### 质量单据新增

来料报检单（QM01）、产品报检单（QM02）、来料检验单（QM03）、产品检验单（QM04）的新增、删除（含删除前的报检单弃审）走 U8 质量管理的卡片组件，登录子系统 `QM`。

- **组件**：QM01 `UFQMCo.clsArrInspectCO`、QM02 `UFQMCo.clsProInspectCO`、QM03 `UFQMCo.clsArrCheckCO`、QM04 `UFQMCo.clsProCheckCO`。`Init(login, conn, bOutTrans=false)` 按引用 {0,1,2}，返回 `VT_EMPTY`（不是失败）。`VoucherOperate(DomHead, domBody, actionType, error, voucherid)` 按引用 {0,1,3}，`actionType` 为 `add`、`delete`、`confirm`、`unconfirm`、`update`、`load`。
- **错误**：返回值不可信（`error` 里已有错误时仍可能是 true）；成功 = `error` 为空且没有 COM 异常。U8 报错在 `error` 出参（「错误列表：」后逐条「[描述]:…」）或 COM 异常的 `<ErrBagList><ErrBag … description="…"/>`（取第一个 `description`）。桥都按 409 `u8_rejected` 返回原文。
- **DOM**：新增用空白 DOM（`select * from <视图> where 1=2`）：QM01 `QM_QARRINSPECTB` / `QM_QARRINSPECTT`，QM03 `QM_QARRCHECKB` / `QM_QARRCHECKT`（表体是检验项目），QM02 / QM04 `QM_QPROINSPECTB/T`、`QM_QPROCHECKB/T`。表头行和表体行都要带 `editprop="A"`，否则「表体行数必须大于0行！」。删除、弃审传从同一视图按 `ID` 读出的 DOM、`voucherid` 为 ID 字符串（空白 DOM 报「参照来源单据已被他人修改或删除」）。
- **事务**：QM01 新增在调用方连接的事务里，回滚能撤掉全部；没有外层事务时**失败**的新增也会留下到货行 `fInspectQuantity` 的增量（U8 先回写再校验），所以桥把报检单新增包在事务里。QM03 新增和删除、QM01 弃审和删除由组件自己提交，不能包事务：桥先查完条件，调用后在新连接上回读，调用异常或回读不清 504 `outcome_unknown`。
- **单号**：QM01 由 U8 按编号规则重新编号，但送入时不能为空（「'单据编号'不能为空！」），允许手工改号时占位号可能被当成真单号；QM03 照收送进去的 `CCHECKCODE`。所以两者都由桥按账套的单据编号规则取号（前缀 + 日期 + 流水），事务回滚后编号不退。
- **必输字段**：单据模板里 `voucheritems.IsNull = 1` 的字段（VT 351 到 354）。账套可把表头扩展自定义项（如 `chdefine11`–`chdefine13`，标签如「复核人编码」）设为必输；它们不在视图里，作为 DOM 属性送进去 U8 照收。桥按模板读出清单，能推的自己填，其余缺的一次列出，400「缺少必输字段 x（U8 单据模板设置为必输）」。
- **QM01 字段**：表头 `CVOUCHTYPE` QM01、`CSOURCE` 到货单、`CSOURCEID` / `CSOURCECODE`、`DDATE`、`DARRIVALDATE`、`CVENCODE`、`CDEPCODE`、`CINSPECTDEPCODE`、`IVTID` 351、`CCHECKTYPECODE` `ARR`、`CMAKER`（操作员**姓名**，标签「报检人」）、`CINSPECTCODE`；表体 `SOURCEAUTOID`（到货行 `Autoid`）、`CINVCODE`、`FQUANTITY`、`CWHCODE`、`ITESTSTYLE`。保存后 U8 按 `bArrInspectAutoVerify` 自动审核（`CVERIFIER` 为操作员），到货行 `fInspectQuantity` 加本次数量，新 `ID` / `AUTOID` 写回 DOM。
- **QM03 字段**（一张一个存货）：`CCHECKCODE`、`INSPECTID` / `CINSPECTCODE` / `INSPECTAUTOID`（报检单）、`SOURCEID` / `SOURCEAUTOID` / `SOURCECODE`（到货单）、`CDEPCODE` + `CDEPNAME`（检验部门，U8 校验名称）、`CCHECKPERSONCODE`（检验员）、`PROJECTID` + `CPROJECTCODE` + `CPROJECTNAME` + `CCHKNORMALCODE`（检验方案 `QMCHECKPROJECT`；存货到方案的对照 `QMINVPROJECTS` 可能为空）、`FQUANTITY`、`FREGQUANTITY` / `FCONQUANTIY` / `FDISQUANTITY`、`FDTQUANTITY`（抽检量，`ITESTSTYLE=3` 时必须大于 0；不是 `FCHECKQTY`）、`CCHKCONCLUSION`、`ISWFCONTROLLED`（非空；有启用的审批流时 1）、`IVERIFYSTATENEW` 0，另有一组标志列置 0。表体每行一个方案项目（`QMCHECKPROJECTS`，至少 1 行）：`CCHKITEMCODE`、`CCHKGUIDECODE`、`CSTANDARD`、`CCHECKVALUE`、`CTARGETQJUG`，计量单位组 `CGROUPCODE` 与单位 `CGCOMUNITCODE` 要么都给要么都不给。保存后报检单行 `FSUMCHECKQTY` 加本次数量、检完 `BFLAG=1`；新检验单 `IsWfControlled=1`、`iVerifyStateNew=0`，照常提交审批。
- **删除**：QM03 删除后报检单行 `FSUMCHECKQTY`、`BFLAG` 回 0。已审核的 QM01 删除报「单据已审核不能删除！」，要先 `unconfirm`（清 `CVERIFIER` / `DVERIFYDATE`）、重新读 DOM 再 `delete`；到货行 `fInspectQuantity`、`fInspectNum`、`bInspect` 回 0。弃审后删除失败时不能恢复审核：504 `outcome_unknown`，消息注明「报检单已弃审且未删除」，只能再次删除或在 U8 客户端处理。
- **报检单不能经接口审核**：`confirm` 总是失败（「单据审核失败！」）：组件的审核语句要写 `IVERIFYSTATE`，而报检单表 `QMINSPECTVOUCHER` 没有这一列。U8 只在保存时按 `bArrInspectAutoVerify` / `bProInspectAutoVerify` 自动审核报检单；`unconfirm` 正常。所以桥对报检单的单独审核 400；产品报检单可单独弃审（见「质检单修改」）。
- **到货单的 `bInspect`** 是「已全部报检」（`fInspectQuantity` = `iQuantity`），可以分次报检（参照视图 `QM_QREFARR` 只列 `binspect=0` 且有剩余的行），桥对已全部报检的行 409。「要检验」是 `bGsp`（存货 `bPropertyCheck`）；产品报检只针对 `QcFlag=1` 的生产订单行。

### 不良品处理单

来料不良品处理单（QM05，VT 355）、产品不良品处理单（QM06，VT 356）的新增、审核、弃审、删除，登录子系统 `QM`。组件是基于 VO（`UFQMVOCom.clsVoucherVO`）的接口：QM05 `UFQMCo.clsArrRejectCO`、QM06 `UFQMCo.clsProRejectCO`。

- **初始化**：`Init()` 不带参数；`bOutAuth`、`LoadTemp` 设 True，`bOutTrans` 设 False（U8 自己开、提交事务；设 True 而外部不包事务时保存可能半截落库）。`GetVtidList(login, False)` 按引用 {0,1}，返回的记录集用完组件前不要释放，也不要赋给 `VO.TemplateData`（报 91）。`GetVTID(login, 355|356, 0, vo, False)` 按引用 {0,2,3}。
- **新增**：先用 `QMREJECTVOUCHER` / `QMREJECTVOUCHERS` 的空白 DOM 各挂一行 `z:row`，`vo.InitByXml(头, 体)`（否则 `HaveData=false`，后续每一步都「成功」但什么也不做）；`AddNewVoucher(login, vo, "")` {0,1} 盖上 `DDATE`、`CMAKER`、`CVOUCHTYPE`；用客户端游标（`CursorLocation=3`，`Open(…, 3, 4)`）打开 `select * from QM_QARRCHECKB`（QM06 `QM_QPROCHECKB`）`where ID=? and isnull(PU_CBCLOSER,'')=''`，`AddVoucherByRef(conn, vo, rs)` {0,1,2} 带入 `CHECKID`、`CCHECKCODE`、`CINVCODE`、`FSUMQUANTITY`（= 检验单不良数量）、`CSOURCE`、`SOURCEID`、`CWHCODE`、`DCHECKDATE`、`CVENCODE` 等；改完 `vo.domHead` / `vo.domBody` 再 `vo.InitByXml` 交回；`AddVoucher(login, vo)` {0,1} 保存。
- **表头**：`editprop="A"`、`IVTID`、`CREJECTCODE`、`ISWFCONTROLLED`。属性名一律照 `voucheritems.FieldName` 的原样大小写：U8 只读全大写的 `ISWFCONTROLLED`，送 schema 里的 `IsWfControlled` 会写入 NULL 并报「不能将值 NULL 插入列 'IsWfControlled'」。U8 只在审批流启用时写 1、不启用时不写，且照存送来的值，所以桥按 QM05 / QM06 是否有启用的审批流程（`AuditBizObjectFlows.nStatus=3`）写 1 或 0。扩展自定义项 `chdefine11`–`16` 的 `FieldName` 是小写（如 `chdefine15`，界面标题取 `UserDef`，如「处理类型」）：送大写会被忽略，设为必输时报「'处理类型'不能为空！」；送小写时 U8 自己写 `QMREJECTVOUCHER_extradefine`。缺必输字段时桥 400，写请求字段名和界面标题，如「缺少必输字段 chDefine15（处理类型）」。
- **表体**：每行 `editprop="A"`、`FQUANTITY`、`IDISPOSEFLOW`（处理流程）、`CSCRAPDISCODE` + `CSCRAPDISNAME`（`QMSCRAPDISPOSE`）、`CREASONCODE` + `CREASONNAME`（`Reason`，质量管理类 `iReasontype=1`；U8 核对的是名称）、`CINVCODE`；处理流程 2（降级）另写 `CDIMINVCODE`、`FDIMQUANTITY`。U8 参照生单不带部门和计量列，桥按 U8 客户端的结果补：`CDEPCODE` = 检验单所属报检单的部门（读不到用检验单 `CINSPECTDEPCODE`）；检验单有辅计量时 `FNUM` = 数量 / 检验单 `FCHANGRATE`（按件数小数位 `iNumDecDgt`）；降级行 `CDIMUNITID` 取处理后存货的库存单位（`cSTComUnitCode`，没有时主计量单位），`FDIMCHANGRATE` 取 `ComputationUnit.iChangRate`，`FDIMNUM` = 处理后数量 / 换算率，处理后存货无换算（`iGroupType=0`）时不写这三列；有辅计量而不送时 U8 报「降级后存货必须输入降级后件数!」等。多行处置时其余行由 U8 留下的那一行复制，去掉 `AUTOID`、`IROWNO`、`CBSYSBARCODE`、`UFTS`。
- **单号**：`AddVoucher` 不取号（空时「单据编号不能为空」）。U8 的取号是 `UFQMCo.clsCommon.GetBillNumber(login, vo, "QM05"|"QM06", 单号)`（按引用 {0,1,2,3}，前缀 + 处理日期 + 4 位流水）；桥按单据编号规则取号（`QmNo` / `BillNo`），取了不退。
- **保存结果**：U8 把检验单 `QMCHECKVOUCHER.BREJFLAG` 置 1，在自己的连接上提交，请求连接的事务撤不掉。桥不包事务，预演在取号和保存之前停；保存后在新连接上按 `CHECKID` / `CREJECTCODE` 确认，核对表体行数和 `BREJFLAG`，不清或不符 504 `outcome_unknown`，不要盲目重投。删除后同样核对表头、表体已不在、`BREJFLAG` 已回 0。
- **审核、弃审、删除**：同样的前奏，`GetTheVoucher(login, vo, id)` {0,1} 载入，再 `AuditVoucher` / `UnAuditVoucher` / `DelVoucher(login, vo)`（{0,1}，返回布尔，自己提交）。审核写 `CVERIFIER`、`IVERIFYSTATE=1`；删除后 U8 回退 `BREJFLAG`。找不到单据时 ErrBag「指定单据不存在或没有该单据数据权限！」，桥回 404；其他拒绝 409 `u8_rejected`。
- **档案**：`QMSCRAPDISPOSE` 标准有 `Sys01` 拒收（流程 0）到 `Sys13`，可能另有自定义项（如升级，流程 2）。`Reason` 里可能没有质量管理类原因，可经 `archives/create`（`archive=reason`）建一条。

### 其他报检单、其他检验单

其他报检单（QM11，VT 361，卡片 `QM_QOthInspect`）、其他检验单（QM15，VT 365，卡片 `QM_QOthCheck`）与来料 / 产品报检单、检验单同表（按 `CVOUCHTYPE` 区分），`CCHECKTYPECODE=OTH`，没有来源单据（来源列为 NULL）、审批流和出入库单。检验单 `INSPECTID` / `INSPECTAUTOID` 指向报检单和表体行。

- **组件**：`UFQMCo.clsOtherInspectVoucherCO`、`UFQMCo.clsOtherCheckVoucherCO`，接口同不良品处理单（没有 `Init(login, conn, bOutTrans)` 和 `VoucherOperate`）：`GetVtidList`、`GetVTID(login, VT, 0, vo, False)`、`InitByXml`、`AddNewVoucher`、`AddVoucher`、`GetTheVoucher` + `AuditVoucher` / `UnAuditVoucher` / `DelVoucher`。其他报检单组件只有 `LoadTemp`（桥只设它为 True）；其他检验单组件另设 `bOutAuth=True`、`bOutTrans=False`，并有 `AddVoucherByRef(cnn, vo, rst, [login])`。可选尾参数桥不传。保存、审核、弃审、删除都在 U8 自己的连接上提交。
- **空白 DOM**：视图 `QM_QOthInspectB/T`、`QM_QOthCheckB/T`（`…B` 表头、`…T` 表体）。属性名照 `voucheritems.FieldName` 的原样大小写。
- **其他报检单新增**（`vouchers/create`）：表头 `CVOUCHTYPE` QM11、`IVTID` 361、`CCHECKTYPECODE` OTH、`DDATE`、`CTIME`、`CINSPECTDEPCODE`、`CMAKER`（操作员姓名）、自定义项；表体 `CINVCODE`、`FQUANTITY`、`ITESTSTYLE`、`IORDERTYPE` / `BEXIGENCY` 0，存货有换算时 `CUNITID` + `FCHANGRATE` + `FNUM`。U8 可能把桥取的单号顺延一位（审计记「U8 换了单号」），桥按调用前最大 ID 与操作员回读。`QM.bOtherInspectAutoVerify` 为 True 时 U8 客户端保存即审核，`AddVoucher` 却不审；桥在选项为 True、操作员有审核权限（`QM02060105`）时保存后补审核，失败不改变新增结果（200 带 `id`、`code` 和 `auto_verify_error`，之后用 `vouchers/verify` 补审）。失败的新增是否留下半截数据未验证。
- **其他检验单生单**：`AddNewVoucher` 后（`QmOthSpec.CheckByRef` 为真时）用客户端游标打开列表视图 `select * from QM_QOTHINSPECTLIST where AUTOID=? and ID=?` 交给 `AddVoucherByRef` 带入（不能用只有表体的 `QM_QOthInspectT`），再按来料检验单的字段集改写表头和检验项目（检验部门 `CDEPCODE` 缺省取检验员的所属部门，不用报检部门；`CINSPECTPERSON` = 报检单制单人；`ISWFCONTROLLED` / `IVERIFYSTATE` / `IVERIFYSTATENEW` 0）。有的账套上 `AddVoucherByRef` 报「单据体行不存在！」，桥遇到这一句就重新 `AddNewVoucher`、表头表体全由桥填。U8 不写报检单行的 `FSUMCHECKQTY`；「已检」是行 `BFLAG=1`，未检可能是 NULL。
- **审核**：其他检验单 `IVERIFYSTATE` 随审核置 1（`IsWfControlled`、`iVerifyStateNew` 恒为 0）；其他报检单表没有 `IVERIFYSTATE` 列，但 VO 组件能审核、弃审。两者桥都开放 `vouchers/verify`（报检单弃审要求还没有其他检验单）；删除已审核的先 `UnAuditVoucher`。
- **功能 id**（`UA_Auth`）：其他报检单查询 `QM02060101`、新增 `QM02060103`、删除 `QM02060104`、审核 `QM02060105`、弃审 `QM02060107`；其他检验单查询 `QM02060201`、新增 `QM02060203`、删除 `QM02060204`、审核 `QM02060205`、弃审 `QM02060207`。

### 质检单修改

来料检验单（QM03）、产品检验单（QM04）、其他检验单（QM15）、其他报检单（QM11）的修改（`vouchers/update`，桥 `QmEdit` / `QmOthEdit`），登录子系统 `QM`。产品报检单（QM02）和来料报检单不开放修改。

- **QM03 / QM04**：`clsArrCheckCO` / `clsProCheckCO` 的 `Init(login, conn, False)` → 从 `QM_QARRCHECKB/T`（`QM_QPROCHECKB/T`）按 `ID` 读出 DOM（保留 `UFTS`）→ 表头 `editprop=M` 并改字段 → `VoucherOperate(头, 体, "update", "", ID)`。U8 盖修改人 `CMODIFIER`（操作员姓名）、换 `UFTS`，并**自己提交**（外层事务回滚撤不掉），所以不包 `CoTrans`，预演只做校验。读出的视图不带 `chdefine11`–`16`，而保存时按模板核对必输，桥把 `QMCHECKVOUCHER_extradefine` 上的已有值按模板字段名照抄进表头，请求改了的以请求为准。
- **QM15**：`clsOtherCheckVoucherCO`（同上节的前奏），`GetTheVoucher` 后改 `vo.domHead` / `vo.domBody`（表头 `editprop=M`）→ `vo.InitByXml` → **`UpdateVoucher(login, vo)` {0,1}**，自己提交，盖 `CMODIFIER`、换 `UFTS`。`ModifyVoucher` 返回 true 但什么也不改，不要用。
- **QM11**：`clsOtherInspectVoucherCO`，同样 `UpdateVoucher`。U8 **接受修改已审核的其他报检单**，也**不盖** `CMODIFIER`，闸门全部由桥做（已审核 409，先弃审；已有其他检验单 409）。
- **闸门**（调用前）：QM03 / QM04 `IsWfControlled=1` 且（`iVerifyStateNew<>0` 或已有 `CVERIFIER`）409 `workflow_enabled`；已审核 409（QM15 先经 `vouchers/verify` 弃审，桥不自动弃审）；已入库（`FSUMQUANTITY`、入库行 `iCheckIdBaks`）、已生成不良品处理单（`BREJFLAG`、`QMREJECTVOUCHER.CHECKID`）409。
- **数量**：不改检验数量 `FQUANTITY`（U8 的修改不回写报检单累计）。合格 / 让步 / 不良之和按已有 `FQUANTITY` 核对；有辅计量时件数按单据换算率同时写。
- **让步接收原因**：`CREASONCODE` 是「让步接收原因」。QM15 带原因而没有让步数量 `FCONQUANTIY` 时修改能保存，但审核报「没有'让步接收数量'，'让步接收原因'不能填写！」，所以桥对 QM15 调用前 400；QM03 / QM04 只要求「有让步数量必须有原因」。
- **让步接收核准人**：有让步数量时 U8 可能要求「让步接收核准人」。模板（VT 353 / 354，表头）里核准人编码是 `CYIELDERCODE`（人员编码）、姓名 `CYIELDERNAME`、核准日期 `DYIELDDATE`。`chdefine15` 是处置说明（如「让步接收」），不是核准人。桥（`QmYield`）：修改和生单都收 `cyieldercode` / `dyielddate`，按人员档案写姓名，日期缺省取单据日期或登录日期；来料 / 产品检验单让步数量大于 0 而没有核准人（请求或单据上已有）时 400；让步数量改回 0 时不清这三列。其他检验单只写不要求。
- **回读**：`UFTS` 变了、请求字段都读到、报检单行 `FSUMCHECKQTY` / `BFLAG` 没变才算成功；`UFTS` 没变 409（U8 原文），否则 504 `outcome_unknown`。
- **QM02 不开放修改**：`clsProInspectCO` 的 `VoucherOperate(…, "update", …)` 都失败且不写入：表体行不标 `editprop` 时「表体行数必须大于0行！」，标 `editprop=M`（即使不改字段）时「更新发退货报检单失败！」。QM02 上的 `CMODIFIER` 是保存即审核时盖的，不代表真实修改。产品报检单只开放单独弃审（`vouchers/verify action=unverify`，即 `unconfirm`）。
- **功能 id**：修改与新增、保存同一个 id（来料检验单 `QM02010203`、产品检验单 `QM02020203`、其他检验单 `QM02060203`、其他报检单 `QM02060103`），见 UFMeta `AA_FormButtonAuths` 里 `cButtonKey='Modify'`（卡片 `QM_QM020102_Doc`、`QM_QM020202_Doc`、`QM_QM020602_Doc`、`QM_QM020601_Doc`；`QmEditReq.Auth`）。

## 13. SQL

- 列表：`TOP (?)` 带整数参数；日期参数 `CONVERT(date, ?, 23)`；rowversion 输出 `CONVERT(varchar(20), CONVERT(bigint, ufts))`，比较 `CONVERT(binary(8), CONVERT(bigint, ?))`；水位 `CONVERT(bigint, MIN_ACTIVE_ROWVERSION()) - 1` 在查询前取。
- 现存量 `CurrentStock.fAvaQuantity` U8 通常不填。U8 自己的可用量公式（`ST_GetStockFormula`）：整行冻结（`bStopFlag` 或 `bGSPStop`）为 0，否则 `iQuantity − fStopQuantity`；待入、调拨待入、待出、调拨待出只在库存选项 `bNormalOn*`（非批次）/ `bBatchOn*`（批次）为 True 时计入。
- 聚合子查询不要引用外层查询的列，否则 SQL Server 报 8124。相关条件写在子查询的 `WHERE` 里。
- 销售发票复核后又弃复时，发货行的已复核开票数量 `fVeriBillQty` 有时不会被触发器减回去；比较时加 `0.000001` 容差，并只统计已复核发票。

### 经营管理报表的取数

- 销售发票复核人是 `SaleBillVouch.cChecker`（统计已复核收入看它）；`cVerifier` 是应收审核人。
- 销售成本取存货核算明细 `IA_Subsidiary` 的 `iAOutPrice`（不是 `iOutCost`）、数量 `iAOutQuantity`，条件 `bSale=1`、`bRdFlag=0`。成本按什么单据结转由存货核算选项 `bSaleType` 决定（销售出库单、发出商品、销售发票），对应的单据类型（`cVouType`）不同，如 `32`、`3201`，但都带 `bSale=1`，所以按标志取、不按类型列举。出入库调整单对销售成本的调整行（`bSale=1`、`bRdFlag=1`）不计入。核算明细上没有业务员、部门时取对应销售出库单表头的。
- 应收票据 `AP_Note`（`cFlag='AR'`）：未结的判断是余额 `iRAmount` 不为 0（U8 的结算、贴现、背书、退回都会把它减到 0），本币余额是 `iRAmount_Local`。往来单位取 `cDwCode`，为空时取 `cEndorser`（出票单位可能只记在 `cEndorser` 上）。数据权限和名称都按这个单位。
- 会计期间的起止日期在 `UFSYSTEM..UA_Period`（`cAcc_Id`、`iYear`、`iId`，排除 `bIsDelete=1`）；专用 SQL 登录读不到系统库时（错误 229、208、916、4060）按自然月，并给提醒。
- 数据水位：`GL_accvouch` 没有 rowversion，按本年度凭证的行数、已记账和作废行数、分录内容校验和 `CHECKSUM_AGG(BINARY_CHECKSUM(i_id, ccode, iperiod, md, mc, ibook, iflag))`、整表 `MAX(i_id)` 判断有没有变化（只改辅助项或摘要的修改不改变水位），并附登录操作员的权限指纹 `perm_fingerprint`，权限变了缓存键也变。

## 14. 事务行为汇总

| 组件 | 在请求连接的事务里能回滚吗 |
| --- | --- |
| 销售、采购、库存 CO 的保存、审核、删除、关闭 | 能。但采购 `VoucherSave2` 有时会自行提交 |
| UFAPBO `SaveVouch`、`Sign`、`CancelSign`、`DeleteVouch` | 能；单号记录不退 |
| UFAPBO `clsPub_AP.Sign_PurBill`、`CancelSign_PurBill`（`Init` 传请求连接） | 能（采购、销售发票均已实测） |
| EAI 凭证导入、档案导入 | 不能，自己提交 |
| U8 API 框架（生产订单审核、新增、修改、删除；物料清单新增、修改、删除、审核、弃审） | 不能，自己开事务 |
| 质量管理卡片组件 `VoucherOperate`（报检单新增） | 能。失败的新增不回滚会留下来源累计报检数 |
| 质量管理卡片组件 `VoucherOperate`（检验单新增、修改、删除；报检单删除、弃审） | 不能，自己提交（修改时调用后 `@@TRANCOUNT` 为 1，但回滚撤不掉） |
| 不良品处理单组件 `AddVoucher`、`AuditVoucher`、`UnAuditVoucher`、`DelVoucher` | 不能，在 U8 自己的连接上提交 |
| 其他报检单、其他检验单组件（同族 VO 接口）`AddVoucher`、`AuditVoucher`、`UnAuditVoucher`、`DelVoucher`、`UpdateVoucher` | 不能，按同族组件不包事务；`UpdateVoucher` 实测自己提交 |
| 采购结算 `VoucherCO_PU.CheckSettle`、`bRdBVAutoSettle`、结算单 `Delete` | 能（`bOutTrans=true`） |
| 采购手工结算（与 U8 界面执行的 SQL 一致：`INSERT` + `PU_GetID` + `PU_SettleWriteBKRDS`） | 能（都在请求连接的事务里） |
| 销售 CO 退货申请单（VT 34）的 `Save`、`Delete`、`VerifyVouch` | 不能：转给 .NET 的 `SaVoucherService` 自己开、提交事务，不看 `bManualTrans` |
| 销售 CO 红冲蓝字发票 `GetNegaVouchData` + `Save` | 能（同其他销售发票） |
| 总账记账（`BalanceRule.VouchPostAll`，桥外包 `TransactionScope`） | 能（见「记账」） |
| 总账取消记账（桥按记账公式反做的 SQL） | 能（一个事务，锁同记账） |
| 审批代理、`UFLTMService` | 自己开连接和事务 |

`UFSystem` 里的登录记录、主键计数（`UA_Identity`）和开放接口令牌行不在账套库里，恢复账套库备份还原不了它们，只会留下主键断号。

## 15. 与 U8 客户端行为的对照

以下行为已用 U8 自己的组件或接口在测试账套上对照：

- **他人锁定的销售订单**：U8 销售组件 `VoucherCO_Sa.VerifyVouch` 不看锁定人，他人锁定的订单照样审核成功；锁定只由 U8 界面和本桥的闸门执行（桥对他人锁定的单据修改、删除、审核一律 409）。
- **其他应收单编号**：经 `vouchers/create` 新建的 `ar_bill` 单号是 `YS` + 单据日期 + 4 位流水（与 `VoucherNumber` 卡片 R0 的规则一致），`VoucherHistory` 的流水同步加一（`cSeed` 为空即按全局流水，不按日期重排），与 U8 卡片界面取号同源。
- **汇率导入的登录子系统**：以 `AS` 或 `GL` 登录调 `U8Distribute.iDistribute.ProcessEx` 都能写入，年度取登录年度。
- **盘点单审核**：`USERPCO.VoucherCO.Verify("18", …)` 生成盘盈 / 盘亏单时要一个 VB6 `Collection`（`MakeWheres`），它只能由客户端窗体创建；不经界面传空、按 ProgID 或 CLSID 创建都不行，U8 自己的批量审核 `AutoVerify` 同样报「类型不匹配生单时出错」。盘点单审核只能在 U8 客户端做。
- **信用检查**：打开信用控制后保存销售订单时 U8 自己检查并拒绝（「信用检查不通过」）；信用余额表未重算时只按本单金额比较（见 `api-reference.md`「客户信用」）。

