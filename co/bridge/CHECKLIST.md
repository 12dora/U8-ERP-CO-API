# 桥编码与复审清单

每次改 `co/bridge`，以及每次复审这类改动，提交前逐条核对。一条一事。出处在句末，指向 `docs/` 里的章节或代码文件。

## COM 调用

- 尾部可选的 ByRef 参数不传，也不要传 `Type.Missing`；两种都会 `DISP_E_TYPEMISMATCH`。（`docs/u8-notes.md` §3）
- 必须占位、调用方又用不到的 ByRef 对象槽，传 `new System.Runtime.InteropServices.DispatchWrapper(null)`。普通 `null` 会 `DISP_E_TYPEMISMATCH`。（`docs/u8-notes.md` §3）
- by-ref 只标契约写明的下标，不要多标、不要漏标：`Save` / `VoucherSave2` 的新主键、`Delete` `{0,1}`、整单 `OrderClose` `{0,1}`、按行 `OrderClose` `{0,1,2}`、`GetVoucherNO`、库存 `Update` `{4,5,6,7,8}`、`MakeOutVouch` `{1,2,3,4,5}`、调拨 `Verify` `{2,3,4,5,6,7,8,10,11}`、`ClosePOItems` / `OpenPOItems` `{2,3}`。（`docs/u8-notes.md` §3）
- 新增或改动 U8 COM 调用（ProgID、成员、实参个数、by-ref 下标）时，同步 `SigTable.cs` 的期望签名，`--check-signatures` 靠它核对。（`docs/testing.md` §2；`SigTable.cs`）
- `BodyCheck` 只对单行表体调用：先 `cloneNode(true)` 并删掉其他 `z:row`，再用普通 `ComUtil.Call`，不要加 by-ref。整表多行时 U8 读第 R 行、写第 0 行。（`docs/u8-notes.md` §4）
- 单价键（`itaxunitprice`、`iunitprice`、`itaxrate`）的 `BodyCheck` 不改行，而是换成 `<R K="…" V="…"/>`。把这些 K/V 写回真实行。（`docs/u8-notes.md` §4；`SaleCalc.cs`）
- 一行的顺序：先 `iquantity`，再调用方给出的单价键（有 `itaxunitprice` 用它，否则 `iunitprice`），调用方改了税率才调 `itaxrate`，然后再调一次单价键。返回非空串作 409 `u8_rejected`，原文带回。（`docs/u8-notes.md` §4）
- 表头 `z:row` 的主键属性必须在，值可以是空串。缺了 U8 回「使用 Null 无效」。销售发票写 `sbvid=""`。（`docs/u8-notes.md` §3）
- CO 的 `Save`、`VoucherSave2`、`Insert`、`Update` 都不重算价税，金额和辅数量由桥填。销售走上面的单行 `BodyCheck`；采购按 `btaxcost` 自己算（单价 6 位、金额 2 位、`AwayFromZero`）；库存看调用方这一次送来的字段：只送了 `iPrice` 就反算 `iUnitCost`，否则有单位成本就 `iPrice = round(数量 × iUnitCost, 2)`。（`docs/u8-notes.md` §4）
- `GetDefaultVoucherDom` 只有 schema、没有 `z:row`。自己 `createNode(1,"z:row","#RowsetSchema")` 挂到 `//rs:data`。卡片不是 `"17"` 时不要退回销售订单空白模板，409 `u8_rejected`「U8 没有返回单据模板」。（`docs/u8-notes.md` §3；`SoDom.cs`）
- 用 `local-name()` 等 XPath 之前，先 `setProperty("SelectionLanguage","XPath")` 并声明 `rs`、`z`。属性名按该行集 schema 的大小写写。（`docs/u8-notes.md` §3）
- `ufts` 只去掉右侧空格。去掉左侧空格后，撤销提交会报单据已被别人修改。（`docs/u8-notes.md` §3）
- 不要调用 `MakeVouchByWhere`。销售出库只用 `MakeOutVouch`，第二个参数必须是 `DispatchWrapper`。（`docs/u8-notes.md` §3、§6）
- UFAPBO：`clsAccount_AP.Init` 返回 false 也能用，不要当失败。`GetVouchData` 的模板只有 schema，表头表体行自己挂；`AutoAddBodyRecord` 报「未设置对象变量」，不要用。（`docs/u8-notes.md` §5）
- `SaveVouch` 的 by-ref 分两种：`clsCloseBill`（收付款单）`{0,1,2}`，`clsAPVouch`（应收应付单）两个 DOM 按值传、只有 `{2}`。`Sign` / `CancelSign` / `DeleteVouch` 都是 `{1}`，`DeleteVouch` 尾部的 `Ufts` 不传。条件只发单张 `type='0'`，永远不发 `type='1'`（按日期批量）。（`docs/u8-notes.md` §5）
- 应收单、应付单的每一行表体都要带 `cexch_name` 和 `iExchRate`，只写在表头上保存失败。（`docs/u8-notes.md` §5；`ArapDom.cs`）
- 不要用 `VouchCanSign` 判断能否弃审，它对已制单、已核销只回笼统原文。改在调用前查库：凭证号、手工来源、票据/网银、核销、往来明细、结账。（`docs/u8-notes.md` §5）
- 总账新增用 `renewproofno="y"`，不是 `"true"`；修改用 `proc="edit"` 加 `renewproofno="false"` 和原凭证号，这是整张替换。`proc="delete"` 不支持，作废、审核、签字、删除按 U8 界面实际执行的 SQL 写（实测核对）。（`docs/u8-notes.md` §10）
- 凭证导入之后在事务里补 `GL_CashTable.csign`（导入器留 NULL，U8 界面会填），条件带 `csign IS NULL`。（`docs/u8-notes.md` §10）
- 总账表用 `GL_accvouch` 并显式带 `iyear`，不要用 `gl_v_accvouch`。会计年度取登录日期的年份，请求的 `year` 是账套库年度。（`docs/u8-notes.md` §10）
- 档案修改用 `proc='diffedit'`，但发整条记录：当前行所有有对应列、非空、可写的标签（跳过禁写和统计类）打底，再叠调用方字段；`ArcKind.Resend` 的标签当前值为空时补缺省。U8 在 diffedit 时也按档案设置查必输项，只发改动的标签会被拒。U8 原文含「不可为空」时追加「（U8 档案设置为必输）」。人员删除也要带当前的 `rIDType`。`proc='edit'` 是整张替换，不要用；例外是原因码（`reason`），实测的是 `edit`，桥照样发整条记录（`ArcKind.EditProc`）。（`docs/u8-notes.md` §11）
- 不要用 EAI 的 `proc='Query'`：它把结果文件写进 U8 安装目录的 `Logs`。档案读取走 SQL。桥对 `u8Home` 只读（EAI 对照表、`U8Resolve` 的程序集），不写不删。（`docs/u8-notes.md` §11；`U8Resolve.cs`）
- 采购发票的 `VoucherCO_PU.Init` 单据键用 `purbill`（01 专用）/ `ppurbill`（02 普通），不是 `01` / `02`；到货单用 VT 2 和 `"0"`。空白 DOM 取 `GetVoucherDataById(头, 体, "", 0, "")` 的 schema 再挂行，不要用视图 `where 1=2`，否则 `VoucherSave2` 报「类型不匹配」。（`docs/u8-notes.md` §7）
- 采购发票新行写 U8 自己加载出的发票的整套属性，布尔写 `True` / `False`，不写 `1` / `0`。普通发票（02）照 U8 自己保存的普通发票：税率 0、`idiscounttaxtype=1`、`btaxcost=False`。（`docs/u8-notes.md` §7；`PuInvBody.cs`）
- 生产订单审核：每次新建 `U8EnvContext` 和 `U8ApiComBroker`，用完 `Disconnect`，不包 `CoTrans`。`GetLastError` 只取第一行、去掉 .NET 异常类型名。报 IPC 失败是 `U8MPool` 没起，返回 503 `u8_unavailable`，不要当 409。（`docs/u8-notes.md` §9）
- 生产订单新增：`Connect("U8API/MOrder/MOrderAdd")` 之后才 `GetExtBoEntity("extbo")` 建表头和 `Mom_OrderDetail` 行，不送 `Mom_MoAllocate`（U8 按标准 BOM 展开）。新 `MoId` 从 `ext.Serialize()` 的 `<mom_order moid>` 取；拿到后在新连接上核对该单存在且制单人是本操作员；拿不到或调用异常时，只接受调用前 `max(MoId)` 之后、本操作员建的、所有行（序号、存货、数量、部门、类别、仓库、备注、起止日期）和创建时间都对得上的唯一一张，否则 504。集合生产订单（`CollectiveFlag<>0`）不删。（`docs/u8-notes.md` §9）
- 生产订单修改：`MOrderUpdate` 把每一行的子件全部删掉再按送去的 `Mom_MoAllocate` 重插（`AllocateId` 全换新，实测），不送就是清空用料。只能 `MOrderLoad` 取整张订单、在加载出的实体上改、交给 `MOrderUpdate`，子件全部重送；用量、数量、换算率按快照原值写回（Load 舍入过）；改了数量的行按 U8 自己的算法重算（`MoUpdateAlloc`），不要写成 N/D × 数量。不开放开工日期；有产出品子件的行不改完工日期（实测 U8 重建子件时沿用旧需求日期）。Load 之后先 `Disconnect`，再新建 `U8EnvContext` / `U8ApiComBroker` 连 Update（同一个 broker 重连实测报「ConnectionString 属性尚未初始化」），同一时刻不连两个。调用前：按 `AllocateId` 引用子件的行（`MoUpdateRefs`）存在、或子件有桥保不住的非缺省设置（`MoUpdateCols.Guard`）都 409；把要写回的快照值和旧 `AllocateId` 写进审计事件 `mo_update_snapshot`。U8 提交后桥在自己的事务里确认子件已重插、没被别人改过，再按（行、行号、存货）一对一写回 `MoUpdateCols.Restore` 各列和 `UpperMoQty` 原值（`MoUpdateRestore`），然后逐行、逐子件核对，不符一律 504；Update 报错也要回读，未改才报 409 / 503。不要自己 `NewItem` 重建子件。（`docs/u8-notes.md` §9「生产订单修改」）
- 已审核、已领料的生产订单修改：`MOrderUpdate` 不改领料单的 `rdrecords11.iMPoIds`（实测留下指向已删子件的引用）。调用前抓全部材料出库引用写进审计事件 `mo_update_refs`，改后需用量低于已领量、替代料领用都 409；U8 提交后先 `MoUpdateRemap` 按（行、行号、存货）把引用改到新 `AllocateId` 并核对（同存货、无旧 id 残留、已领量不变且不超过新需用量），再 `MoUpdateRestore`；任何一步不符回滚并 504，审计里留旧新对照 `mo_update_remap`。（`docs/u8-notes.md` §9「已审核、已领料的订单」）
- 物料清单（`BomAdd` / `BomUpdate`）：每行都送全 B2 那组标志（`DFVFlag`…`DCostWIPRel`），缺了报 `CheckStructureIntegrity`；新 `BomId` 按（母件 `PartId`、版本、`BomType=1`）在新连接上查；修改必须 `UpdateByDiff=true` 且送全部行（没送的行号被删），桥自己拒绝已审核的；删除前查 `mom_orderdetail` / `OM_MODetails` / `AssemVouch` / `MatchVouch(s)` / `TransVouch` 的 `BomId`（U8 不查）。审核、弃审、删除的 `versionoridencode` 是版本号字符串。（`docs/u8-notes.md` §9「物料清单」）
- 审批用 `DoSubmit`、`UndoSubmit`、`Audit2`、`Abandon2`、`SubmitResubmitMessage2`。不要用 `SubmitApplicationMessage2`、`GetUnAuditTasks`、`GetTasks2`。代理和 `UFLTMService` 自己开事务，不要再包 `CoTrans`。（`docs/u8-notes.md` §12）

## 事务

- 请求连接上的每一次 U8 写都放进 `CoTrans.Begin`。成功走 `CommitSeen`，提交前的异常走 `Rollback`。没有活动事务只认 HRESULT `0x8004D00E`。（`docs/architecture.md` §5）
- `CommitSeen` 之后的回读（`EditMsg.Saved`、按单号找主键、关闭状态）失败时，一律 504 `outcome_unknown`。消息写明已经保存，并带上已知的新主键或单号。不要改成 500，也不要让调用方把同一笔写再投一次。（`docs/architecture.md` §5；`EditMsg.cs`）
- 审核提交后还要确认本连接 `@@TRANCOUNT` 为 0，并在新连接上读到目标状态。这道回读不要加 `NOLOCK`。（`docs/architecture.md` §5）
- 提交前的回写核对（累计开票、累计到货等）一律包在 `StockCall.AfterCheck` 里。U8 在保存里已自行提交（`@@TRANCOUNT` 为 0）时核对失败回滚不了，要返回 500「U8 已自行提交，无法核对回写」并在审计 `detail` 记细节，不能报 409 让调用方以为没写进去。（`docs/u8-notes.md` §7；`StockCall.cs`）
- 自己提交的组件不要包进 `CoTrans`：凭证导入 `U8PzInsert.Transact`、档案 `U8SrvTrans.IClsCommon.Transact`、生产订单的 U8 API。调用前把能查的都查完，调用后在 `ctx.OpenFresh()` 上回读；调用抛错或回读失败一律 504 `outcome_unknown`。（`docs/u8-notes.md` §14）
- 按 U8 界面实际执行的 SQL 写的总账操作：同一事务里 `UPDLOCK, HOLDLOCK` 读状态、过闸门、写、再读一遍，状态没到就回滚。只动手工凭证的地方要查 `coutsysname`。（`docs/u8-notes.md` §10）
- 记账（`GlPost`）走 U8 的总账 .NET 组件：`HttpLoginContext.UserData` 只在写线程上设、`finally` 里清；外层 ReadCommitted `TransactionScope`（超时 5 分钟），事务第一条语句 `UPDLOCK, HOLDLOCK, TABLOCK` 锁 `GL_mpostcond1` 并重查，Complete 前核对未升级 MSDTC；桥自己的 SqlClient 读写用交给 U8 的同一个串、同一时刻只开一条（否则升级 MSDTC）；`VouchPostAll` 前快照、后按 `i_id` 改回它不带年度误写的记账标志并核对；`ctx.Conn`（ADO）不在事务里，事务期间不要用它读写 `GL_accvouch`（会等自己的锁）。（`docs/u8-notes.md` §10「记账」）
- UFAPBO 的 `SaveVouch`、`Sign`、`CancelSign`、`DeleteVouch` 在请求连接的 `CoTrans` 里能回滚，照常包事务；但 `VoucherHistory` 的号回滚后不退。（`docs/u8-notes.md` §5）

## 写预演

- 自己提交的组件（凭证导入、EAI 档案导入、U8API 生产订单 / 物料清单、审批服务、检验单保存和删除、记账 `TransactionScope` 等）调用前一行写 `DryRun.Stop(ctx, "<组件>")`：放在全部检查之后，它之前不能有任何写入。（`docs/architecture.md`「预演（`dry_run`）」）
- 回滚模式的路由，每一笔写（COM 或 SQL）都在 `ctx.Conn` 上经 `CoTrans`；第一次提交之前不要在 `OpenFresh` / `AdoXml.Open` 开的新连接上写。提交钩子只回滚提交的那条连接。（`docs/architecture.md`「预演（`dry_run`）」；`DryRunRun.cs`）
- 新单据类型、类型的新操作要在 `DryRunModes` 显式加一行，缺省是拒绝预演；`--selftest` 的 `DryRunSelfTest` 核对 `Kinds.cs` 与模式表一致。（`DryRunModes.cs`）
- 包住提交的 `catch` 要先放行预演结果：`catch (DryRunDone) { throw; }` 写在转换异常的 `catch` 前面；不要吞掉异常继续走。（`DryRun.cs`）
- 只为预演做的回读（`DryRun.Set` 的数据、登记单据用的查询）失败时不能让预演失败：接住、跳过或在 `detail` 里标出。（`DocMark.cs`）
- 新单据在事务里拿到主键后马上 `DryRun.Created`（或 `DocMark.Created`），改到请求之外的已有单据用 `DryRun.Touched`；预演的 `docs` 只读登记过的单据。（`DryRunPreview.cs`）

## DOM

- 会 `FinalReleaseComObject` 的辅助函数返回之后，变量里的 `z:row` 全部失效。重新选取，不要沿用。（`docs/u8-notes.md` §3）
- 新增行可以 `cloneNode(true)` 一张已加载行来得到合法节点，但不要把种子行的数据整行留下。只保留外键、`irowno`、`editprop`、调用方这次送来的字段，以及桥算出的单位和金额。数量、单价、金额、辅计量、批次、货位、保质期、自由项、自定义项、备注、关闭人、累计、来源关联、条码、`ufts` 一律清掉。（`docs/u8-notes.md` §3）
- 调拨新增行保留表头上的 `cTVCode`，新行都要带调拨单号。数量变化且行上已有 `iTVACost` / `iTVPCost` 时，重算对应金额。（`docs/u8-notes.md` §8；`StockDomXfer.cs`）
- 每个 DOM 的 schema 只读一次，之后调用 `DomRows.Set(dom, row, name, value, schema)`。不要每缺一个属性就重读整份 schema。（`DomRows.cs`）
- 白名单里的名字若不在该 DOM 的 schema 中，返回 400「未知字段 x」。表头覆盖若名字不在表头 schema 里，同样 400「不能设置字段 x」。发货单表头 `cwhcode` 只作为行仓库的缺省，不要写进表头 DOM。（`docs/api-reference.md` §7、§11）
- 修改人、修改日期这类戳记，schema 里没有该属性就不要设。（`docs/u8-notes.md` §3）
- 空白 DOM 用 `select … where 1=2` 再 `Recordset.Save(dom, 1)`，然后自己补 `z:row`。空记录集不要走 `AdoXml.LoadDom`，那会 404。（`docs/u8-notes.md` §3）
- 纯 schema 的 `where 1=2` 空白一律走 `DomRows.Blank`（经 `TplCache` 缓存，每次给新 DOM）。`GetDefaultVoucherDom`、`GetDefaultVTID`、`ArcTpl` 行、CO 组件返回的空白、填过表头的 DOM 不要放进缓存，也不要在缓存命中的 DOM 之外另存实例。（`docs/architecture.md`「空白模板缓存」；`TplCache.cs`）

## 校验与闸门

- 修改和删除只接受未审核、未关闭、没有下游的单据。关闭要求已审核：U8 的 `OrderClose` 会关掉未审核的销售订单，调用前拒绝，409 `state_mismatch`「单据未审核」。（`docs/api-reference.md` §8、§9、§10）
- 已关闭不能再关，未关闭不能打开；按行同样。生单的来源必须已审核且未关闭。销售发票还要拒绝已关闭的发货单和期初（`bFirst`）。（`docs/api-reference.md` §10、§11）
- 审批流已启用或单据在途：409 `workflow_enabled`。到货单的审核和删除都要查。销售发票的复核、弃复、删除拒绝 `iswfcontrolled=1`。生产订单审核拒绝 `IsWFControlled=1` 的行。采购发票的审核是采购复核（`ConfirmBill` / `CancelconfirmBill`），拒绝 `IsWfControlled=1`；取消复核先查应付审核、结算、现付、应付明细。（`docs/api-reference.md` §6）
- `line_id` 和 `source_line_id` 必须是本单的行主键。不属于本单则 400「明细行不存在」。同一请求里重复则 400。把现有行全部删掉又不新增则 400「不能删除全部明细」。（`docs/api-reference.md` §8）
- 数量必须是有限数、大于 0、不超过 1000000000000。明细 1 到 200 行。`line_ids` 写成 JSON `null` 时返回 400，不要当成整单关闭；元素是严格整数（1 到 2147483647），不要把布尔、字符串、浮点收成整数。（`docs/api-reference.md` §10、§11）
- 读 `cinvcode`、`cassunit` 时，null 或空白视为这次没传。只有送来的值与行上现有值不同（去空格、忽略大小写）才清掉旧的单位、换算率和辅数量再重填。库存 08/09/12 的修改若要换存货，400「不能修改存货编码，请删除该行后新增」。（`docs/u8-notes.md` §4；`StockUnits.cs`）
- 可写字段只走该类型的白名单，并与 DOM schema 一致。名单外 400「不能设置字段 x」。自定义项和自由项拒绝前导零（`cdefine01`、`cfree07`）。禁写名单含该类型的主键、单号、审核人、审核日期；销售发票的主键列是 `SBVID`，与 `Json.Blocked` 一致。（`docs/api-reference.md` §7；`Json.cs`）
- 销售出库生单不接受任何表头字段；`lines` 可选（收 `source_line_id`、`quantity`，另收 `cbatch`、`cposition`、同一发货行可按批号 / 货位拆行，1 到 200 行，各行合计不超过行剩余可出数量；批号 / 货位在生成、改数量之后的第三步写，同样事务外 `Load`、失败即补偿，见 `StockGenSaleBatch*`），带了就按行部分生成（`StockGenSalePart`，`docs/u8-notes.md` §8），不带整单生成。生单分两步、不在一个事务里：先 `MakeOutVouch` 整单生成（在 `StockCall.RunAt` 的桥事务里调用，生产审计 `@@TRANCOUNT` 调用后仍为 1，不自行提交），由桥 `CommitSeen` 提交，生成量多于要的（部分生单，或以前部分生单后 U8 按原剩余重生）再在新事务里修改或删除多出的出库单并核对 `fOutQuantity`，失败就删掉本次生成的全部出库单。`USERPCO.Load` 不能在桥的事务里调用（它走 U8 自己的连接，看不到未提交的单据或互相等锁）。发货单必须已审核且剩余数量大于 0。`MakeOutVouch` 前后读取该 `cDLCode`（发货单 `DLID` 的字符串）在 `rdrecord32` 上的 `max(ID)`；没有更大的新 id 则 409「U8 没有生成销售出库单」。按仓库拆成多张时，响应另加按 `ID` 升序的 `ids`。（`docs/u8-notes.md` §6）
- 删除按类型挡住下游：发货单已被销售出库或销售发票引用、销售出库已有发票行指向、采购入库已被采购发票或采购结算引用、其他入出库的来源不是库存、到货单已报检或已入库。来源是调拨的其他入出库不能改。调拨单、形态转换单、盘点单审核生成的其他入出库单不能单独弃审、修改、删除（`StockMisc.MadeBy`，判断看 `cSource` / `cBusType`）；来源单弃审前生成单已审核时 409（`StockMisc.RefuseUndo`）。`Inventory.bPropertyCheck=1` 的存货不能直接采购入库。销售发票复核后又弃复、发货行已复核开票数量对不上时 409，请到 U8 客户端处理。（`docs/api-reference.md` §9；`docs/limitations.md`）
- 红字销售发票（`bReturnFlag=1`）的修改一律 400「仅支持蓝字销售发票」。删除、复核、弃复只放行参照退货单生成的红字发票（`iDisp=1`、没有发货单 `SBVID` 反指、每行都指向退货单行），VT 用红字的 1 / 3；其余红票（先开票等）400。都在任何 COM 调用之前按 `CoRows.HeadRow` 的 `red` 判断。红字发票删除同样每行 `editprop=D`，并在同一事务里核对退货行 `iSettleQuantity`、订单行 `iKPQuantity` 退回。（`SaleEditMoreGate.cs`；`SaleGenRedDel.cs`）
- 发货单删除拒绝非蓝字（`cVouchType` 不是 `05` 或 `bReturnFlag=1`）和期初 `bFirst=1`，用语与审核相同。销售发票删除前把每一行 `editprop` 标成 `D`，否则 `iSettleQuantity` 不回退。（`docs/u8-notes.md` §6）
- 销售发票参照发货单生成时，保存前表头写 `idisp=1`、每行写 `cbdlcode` = 发货单号。实测：界面参照发货单开的发票 `iDisp=1`，不写就是缺省 0，U8 当成先开票，之后修改报「先开票不可以参照发货单」。（`docs/u8-notes.md` §6；`SaleGenInv.PinInvHead`）
- 到货单参照采购订单：`VoucherCO_PU.Init` 的第 6 个参数 `sBillType` 必须是 `"0"`。传空串时保存也成功，但 U8 不往临时明细表写行，`iArrQTY` / `fPoArrQuantity` 不回写，之后按 U8 自己的规则删不掉。保存后在同一事务里核对 `iArrQTY` 加了本次数量。（`docs/u8-notes.md` §7）
- 总账写操作先查 `UFSYSTEM..UA_HoldAuth`（本人或角色，或 `admin`）：`GL0201` 填制、`GL0202` 整理/删除、`GL0203` 出纳、`GL0204` 审核。没有是 403 `no_permission`。走 `PermCheck.Require`（与读路由共用 `PermLoad` 的年度窗口：请求年度、建账年度 `UA_Account.iYear`；写路由不走权限缓存），不要另写 `UA_HoldAuth` 查询；凭证用登录日期的年份，两者不要混用。（`docs/u8-notes.md` §10）
- 新的读路由（或读路由新增的单据类型、档案、报表）必须在 `PermRegistry` 登记功能 id 和受控对象，否则 `PermGate` 一律 403。列表的记录级过滤用 `PermSql` 追加参数化条件（放在 WHERE 最后，保证 `?` 顺序），单张读取用 `PermCheck.CheckVoucher` / `CheckRow`；不存在仍回 404。（`docs/api-reference.md` §22）
- 总账修改是删了重插：报文里调用方不能填的列（`memo1` / `memo2`、`reserve1`、按分录号的 `bill_type` / `bill_id` 和业务员）从原凭证原样带过去；抄不回去的要拒绝：红字冲销凭证、带 `cDefine*` 的凭证、已做银行对账或往来两清的凭证、带这些值的分录换了科目顺序。取消出纳签字只能取消本人的。（`docs/api-reference.md` §14；`GlCarry.cs`）
- 应收应付新增：外币必须给 `iExchRate`；单据月份应收/应付已结账（`GL_mend.bflag_AR` / `bflag_AP`）时拒绝。（`docs/api-reference.md` §12；`ArapCoArch.cs`）
- 生单的来源类型只认 `VoucherKind.Sources`，请求 `source_type` 省略取第一个。锁键用实际来源。（`docs/api-reference.md` §11；`Kinds.cs`）
- 改了数量而调用方没送辅数量（调拨是 `itvnum`）时，用换算率重算辅数量。换存货或换辅计量单位时，先清掉已加载的单位、换算率和辅数量，再按新存货重填。（`docs/u8-notes.md` §4）

## 并发

- 读线程池上的处理函数只用 `ctx.Conn` 和 `ctx.OperatorName`，不能碰 `ctx.Session`（读线程没有登录对象）。需要 `userToken`、CO 或 UFAPBO 的留在写线程池。（`docs/architecture.md` §4；`RouteClass.cs`）
- 新的纯 SQL 路由要在 `RouteClass.cs` 登记；新的写路由要在 `DocLocks.KeysOf` 里有锁键（单据、来源、`new:<类型>`、总账 `gl:…`、档案 `arc:…`）。漏了就会和别的写线程并发改同一张单据或同一段编号。（`docs/architecture.md` §4）
- 登录缓存的键带登录日期。不要为了命中率把日期去掉，否则会绕过 U8 的日期检查。（`docs/architecture.md` §4；`AuthCache.cs`）
- 登录复用（`loginReuse`）打开时登录对象会留给下一笔请求。任何 by-ref 登录槽（`Init`、`Transact` 等把 `login` 按引用交给组件的调用）都要先 `ctx.DropLogin()` 或经 `LoginBack`；改写登录对象属性、可能在请求连接上留下未结束事务的新路径同样先 `ctx.DropLogin()`。登录子系统 QM、AR、AP 的请求由 `StaExec.KeepAfter` 一律不放回，确认无副作用之前不要去掉。（`docs/architecture.md`「登录复用」；`LoginCache.cs`、`StaExec.cs`、`WorkContext.cs`）

## SQL

- 列表 SQL：`TOP (?)` 带整数参数，日期 `CONVERT(date, ?, 23)`，rowversion 输出 `CONVERT(varchar(20), CONVERT(bigint, ufts))`、比较 `CONVERT(binary(8), CONVERT(bigint, ?))`，水位 `MIN_ACTIVE_ROWVERSION() - 1` 在查询前取。（`docs/u8-notes.md` §13）
- SQL 只用参数 `?`。调用方的输入不拼进 SQL 文本。表名和列名只来自 `VoucherKind`，不要用来路字符串当标识符。（`docs/architecture.md` §5）
- 聚合子查询不要引用外层查询的列，否则 SQL Server 报 8124。相关条件写在子查询的 `WHERE` 里，`SUM` 只读子查询自己连接上的表。发票删除的残留检查用内层发货行的 `iTB`，不要写外层别名上的 `itb`；比较时加上 `0.000001`，并只统计已复核发票。（`docs/u8-notes.md` §13）

## 响应与错误码

- 错误体是 `{"ok":false,"code","message"}`。`message` 用中文。U8 拒绝时 409 `u8_rejected`，原文带回。500 对调用方只写「内部错误」。错误一律 `BridgeException(status, code, message)`。（`docs/api-reference.md` §18）
- 成功字段与 API 服务的模型一致，桥多给的字段不要在 API 层丢掉：修改和生单的 `lines`（保存后的行数）、销售发票审核的 `ar_verifier`、调拨审核的 `generated`、一张发货单拆出多张销售出库时的 `ids`、生产订单的 `allocations` 与 `state.closed`。`sale_out` 生单带了非空 `head`，在 API 层就 400；`lines` 可选，原样转给桥。（`docs/api-reference.md` §5、§6、§11）
- 可直接审核的类型是 `VoucherKind.Verifiable` 为真的类型：全部可读取类型去掉质量单据和盘点单（盘点单审核实测 U8 报「类型不匹配生单时出错」，登录前 400，`StockMisc.NoCheckVerify`）（检验单走 `workflow/*`，报检单只读；不良品处理单 QM05 / QM06 不在审批流控制下（`IsWfControlled=0`）时可经 `vouchers/verify` 直接审核、弃审（`QmRejOps`），受控时 409 `workflow_enabled`）。采购发票可以审核，审核即采购复核（`ConfirmBill` / `CancelconfirmBill`）；生产订单可以审核，登录子系统换成 `VerifySub` 的 `MO`。Python 客户端的 `verify --type` 用同一份名单，API 的 `VerifyType` 与之一致。（`docs/api-reference.md` §6；`Kinds.cs`）
- 销售发票的审核人列是 `cChecker`、日期是 `dverifydate`，调拨单、形态转换单、调拨申请单是 `cVerifyPerson` / `dVerifyDate`，盘点单是 `cAccounter` / `dveridate`（不是 `cVerifyPerson`）。不要拿销售订单的 `cVerifier` 去对。（`docs/api-reference.md` §6）
- 应收、应付审核（`arap_verify`）走 `UFAPBO.clsPub_AP.Sign_PurBill` / `Sign_SaleBill`，条件 XML 必须带 `PBVID` / `SBVID`（区分大小写），不能用列表里的 `cLink`：缺了 U8 把 Null 赋给字符串，报「使用 Null 无效」。`xmlMsg` 传空串，不要传 Null。（`docs/u8-notes.md` §5）

## 门禁

- C#（`co/**/*.cs`）：文件非空非注释行不超过 500，函数 NLOC 不超过 60，圈复杂度不超过 10，参数不超过 6。Python：文件不超过 400（`test_*.py` 不超过 800），函数不超过 60，圈复杂度不超过 10，参数不超过 5。没有基线，已有文件超限同样失败，超了就拆开。（`CONTRIBUTING.md`）
- 停在 C# 5：不用 `?.`、`$""`、`nameof`、表达式体成员、`out var`、元组、模式匹配、`using static`。晚绑定 COM 只走 `ComUtil.*`，不引用互操作程序集。（`CONTRIBUTING.md` §4）
- 提交前对改过的 `.cs` 跑 `python3 scripts/quality/cs_metrics.py`，对改过的 Python 跑 `uvx ruff@0.16.9 check --config ruff.toml`。lizard 的退出码不是 0 时，即使标准输出看起来像 CSV，也要判失败。（`CONTRIBUTING.md`）
- 不要写 `# noqa` 或 `ruff: noqa`，除非同一行写明为什么必须豁免、谁同意的。不要跑 `ruff check --add-noqa`。（`CONTRIBUTING.md`）

## 实测

- 文档里没有写过的 COM 调用，先在测试账套核对，再写进 `docs/u8-notes.md` 和桥。不要在正式账套上试，代码里也不要把任何账套写进白名单。（`CONTRIBUTING.md` §1、§5）
- 写操作只在可恢复的测试账套里做，测试删掉自己新建的单据。例外：蓝字发票复核再弃复之后，删除可能因已复核开票数量残留而 409，记了账的凭证也不做取消记账，这两类测试后用备份恢复测试账套。（`docs/testing.md` §3）
- 不要部署第三方 U8 封装包，也不要把那类程序或代码拷进本仓库。（`CONTRIBUTING.md` §1）
- 字符串转日期写 `CONVERT(date, ?, 23)`；要 datetime 时再包一层 `CONVERT(datetime, CONVERT(date, ?, 23))`。`CONVERT(datetime, ?, 23)` 在 U8 的 SQL Server 上报「从字符串转换日期和/或时间时，转换失败」。
