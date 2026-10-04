namespace U8Co
{
    // PermRegistry 的生产、质检、应收应付、档案、总账、现存量、报表各行。
    internal static partial class PermRegistry
    {
        // 质检单：客户、仓库、部门按可空列处理；供应商只在来料类上必有。存货在表头 CINVCODE。
        // 产品报检、产品检验、两类不良品处理的查询 id 已按 U8 授权目录核对。
        static PermRule[] MoQmRules()
        {
            return new PermRule[]
            {
                V("production_order", "生产订单", A("MO02001Q"),
                    PermObj.B(PermObj.Inventory, "mom_orderdetail", "MoId", "MoId", "InvCode"),
                    PermObj.BOpt(PermObj.Department, "mom_orderdetail", "MoId", "MoId", "MDeptCode"),
                    PermObj.BOpt(PermObj.Warehouse, "mom_orderdetail", "MoId", "MoId", "WhCode")),
                MoWrite(MoClose.CloseRule, "生产订单关闭", A("MO03001CLS")),
                MoWrite(MoClose.OpenRule, "生产订单打开", A("MO03001UNC")),
                // 新增 / 删除（MoCreate / MoDelete）：生产订单输入卡片 MO_Voucher_MO02001 的 tlbAdd（MO02001N），
                // tlbDelete（MO02001D）或生产订单列表 MO_List_MO03001 的「删除」（MO03001D）。
                // MO_Voucher_MO03009 / MO_List_MO02009 是补料申请单，不是生产订单（核对）。
                MoWrite(MoCreate.CreateRule, "生产订单新增", A("MO02001N")),
                MoWrite(MoDelete.DeleteRule, "生产订单删除", A("MO02001D", "MO03001D")),
                // 修改（MoUpdate）：生产订单输入卡片 MO_Voucher_MO02001 的 tlbModify（MO02001M）。
                MoWrite(MoUpdate.UpdateRule, "生产订单修改", A("MO02001M")),
                Inspect("qm_incoming_inspect", "来料报检单", A("QM02010101"), true),
                Inspect("qm_product_inspect", "产品报检单", A("QM02020101"), false),
                Qm("qm_incoming_check", "来料检验单", A("QM02010201"), true, true),
                Qm("qm_product_check", "产品检验单", A("QM02020201"), false, true),
                Qm("qm_incoming_reject", "来料不良品处理单", A("QM02010301"), true, false),
                Qm("qm_product_reject", "产品不良品处理单", A("QM02020301"), false, false),
                // 其他报检单（QM11）、其他检验单（QM15）：UA_Auth「其他报检单查询」QM02060101、「其他检验单查询」QM02060201，
                // 另收列表查询 QM030601 / QM030603（核对）。
                Inspect("qm_other_inspect", "其他报检单", A("QM02060101", "QM030601"), false),
                Qm("qm_other_check", "其他检验单", A("QM02060201", "QM030603"), false, true)
            };
        }

        // 生产订单关闭 / 打开（vouchers/close，写路由，MoClose 用 ForKey 取）：功能 id 取生产订单列表（MO_List_MO03001）
        // 「关闭」「还原」按钮的 MO03001CLS / MO03001UNC（UFMeta AA_FormButtonAuths；生产订单输入卡片没有关闭、还原按钮）。
        // 选中的每一行按读取的数据权限对象判断。
        static PermRule MoWrite(string key, string title, string[] auths)
        {
            return R(key, title, auths,
                PermObj.B(PermObj.Inventory, "mom_orderdetail", "MoId", "MoId", "InvCode"),
                PermObj.BOpt(PermObj.Department, "mom_orderdetail", "MoId", "MoId", "MDeptCode"),
                PermObj.BOpt(PermObj.Warehouse, "mom_orderdetail", "MoId", "MoId", "WhCode"));
        }

        static PermRule Qm(string type, string title, string[] auths, bool incoming, bool hasDep)
        {
            PermObj vendor = incoming ? PermObj.H(PermObj.Vendor, "CVENCODE") : PermObj.Opt(PermObj.Vendor, "CVENCODE");
            if (!hasDep)
            {
                return V(type, title, auths, PermObj.Opt(PermObj.Customer, "CCUSCODE"), vendor,
                    PermObj.Opt(PermObj.Warehouse, "CWHCODE"), PermObj.H(PermObj.Inventory, "CINVCODE"));
            }
            return V(type, title, auths, PermObj.Opt(PermObj.Customer, "CCUSCODE"), vendor,
                PermObj.Opt(PermObj.Warehouse, "CWHCODE"), PermObj.Opt(PermObj.Department, "CDEPCODE"),
                PermObj.H(PermObj.Inventory, "CINVCODE"));
        }

        // 报检单（QM01 / QM02）：功能 id 取报检单列表（QM_QM03010101_List / QM_QM03020101_List）「voucher」按钮的
        // QM02010101 / QM02020101（UFMeta AA_FormButtonAuths，与检验单的 QM02010201 / QM02020201 同一取法）。
        // 存货、仓库在表体（仓库可空）；客户、部门在表头可空，供应商只在来料报检单上必有。
        static PermRule Inspect(string type, string title, string[] auths, bool incoming)
        {
            PermObj vendor = incoming ? PermObj.H(PermObj.Vendor, "CVENCODE") : PermObj.Opt(PermObj.Vendor, "CVENCODE");
            return V(type, title, auths, PermObj.Opt(PermObj.Customer, "CCUSCODE"), vendor,
                PermObj.Opt(PermObj.Department, "CDEPCODE"),
                PermObj.B(PermObj.Inventory, "QMINSPECTVOUCHERS", "ID", "ID", "CINVCODE"),
                PermObj.BOpt(PermObj.Warehouse, "QMINSPECTVOUCHERS", "ID", "ID", "CWHCODE"));
        }

        // 收付款单、应收应付单：往来单位在 cDwCode（应收是客户、应付是供应商）；科目 cCode 不按 code 对象过滤。
        // 部门、业务员在这些单据上常为空，按可空列处理。
        // 应收单 / 应付单：U8 授权目录（核对）里「应收单查询」AR21101、「应收单查询列表」AR0601021，应付 AP21101 / AP0601021；
        // 原先登记的 AR010301 / AP010301 是「远程发送」，已去掉。
        static PermRule[] ArRules()
        {
            return new PermRule[]
            {
                Ar("ar_receipt", "收款单", PermObj.Customer, A("AR0601031", "AR22101")),
                Ar("ap_payment", "付款单", PermObj.Vendor, A("AP0601031", "AP22101")),
                Ar("ar_bill", "应收单", PermObj.Customer, A("AR21101", "AR0601021")),
                Ar("ap_bill", "应付单", PermObj.Vendor, A("AP21101", "AP0601021")),
                // 供应商退款、客户退款：U8 在「付款单据录入」/「收款单据录入」里录入，功能 id 与付款单、收款单相同（UA_Auth 核对）。
                Ar("ap_refund", "供应商退款", PermObj.Vendor, A("AP0601031", "AP22101")),
                Ar("ar_refund", "客户退款", PermObj.Customer, A("AR0601031", "AR22101"))
            };
        }

        static PermRule Ar(string type, string title, string partner, string[] auths)
        {
            return V(type, title, auths, PermObj.H(partner, "cDwCode"), PermObj.Opt(PermObj.Department, "cDeptCode"),
                PermObj.Opt(PermObj.Person, "cPerson"), OptItem("cItem_Class", "cItemCode"));
        }

        static PermRule Arc(string name, string title, string[] auths, params PermObj[] objs)
        {
            return new PermRule("archive:" + name, title + "查询", auths, objs);
        }

        // 档案：基础档案（ArchiveBaseRules）加只读档案（ArchiveRoRules），再加档案写入（ArchiveWriteRules）。
        static PermRule[] ArchiveRules()
        {
            PermRule[] head = ArchiveBaseRules();
            PermRule[] tail = ArchiveRoRules();
            PermRule[] write = ArchiveWriteRules();
            PermRule[] all = new PermRule[head.Length + tail.Length + write.Length];
            head.CopyTo(all, 0);
            tail.CopyTo(all, head.Length);
            write.CopyTo(all, head.Length + tail.Length);
            return all;
        }

        // 档案写入（写路由，ArcGuard.Permit 按「write:archive:<档案>:<操作>」取）。id 取自 UFMeta 的 AA_FormButtonAuths：
        // 自定义项 Archives_UserDef_List 的 Add / Modify / Delete 是 AS025A / AS025 / AS025D；客户存货对照 Archives_CusInvContra_List
        // 的 Add / Modify / Delete 是 AS1204A / AS1204E / AS1204D（另按客户、存货的数据权限判断编码两段）；计量单位表单
        // （Archives_Unit_List / Archives_Unit_Edit）只有 AS032M。货位、收发类别、本单位开户银行按 U8 授权目录的「货位档案编辑」AS030、
        // 「收发类别编辑」AS016、「本单位开户银行编辑」AS013 登记，新增、修改、删除同一个 id。
        // 之后加的七类：计量单位组用同一表单的 Group 按钮（AS032M）；结算方式 Archives_SettleStyle_List 的 Add / Modify / Delete
        // 都是 AS018。采购类型、销售类型、地区分类、银行档案在 UFMeta 里没有表单按钮可查，不登记，交给 U8。
        static PermRule[] ArchiveWriteRules()
        {
            PermObj[] contra = new PermObj[] { PermObj.H(PermObj.Customer, "cCusCode"), PermObj.H(PermObj.Inventory, "cInvCode") };
            return new PermRule[]
            {
                WriteRule("position", "create", "货位档案新增", A("AS030")),
                WriteRule("position", "update", "货位档案修改", A("AS030")),
                WriteRule("position", "delete", "货位档案删除", A("AS030")),
                WriteRule("rd_style", "create", "收发类别新增", A("AS016")),
                WriteRule("rd_style", "update", "收发类别修改", A("AS016")),
                WriteRule("rd_style", "delete", "收发类别删除", A("AS016")),
                WriteRule("bank", "create", "本单位开户银行新增", A("AS013")),
                WriteRule("bank", "update", "本单位开户银行修改", A("AS013")),
                WriteRule("bank", "delete", "本单位开户银行删除", A("AS013")),
                WriteRule("unit", "create", "计量单位新增", A("AS032M")),
                WriteRule("unit", "update", "计量单位修改", A("AS032M")),
                WriteRule("unit", "delete", "计量单位删除", A("AS032M")),
                WriteRule("unit_group", "create", "计量单位组新增", A("AS032M")),
                WriteRule("unit_group", "update", "计量单位组修改", A("AS032M")),
                WriteRule("unit_group", "delete", "计量单位组删除", A("AS032M")),
                WriteRule("settle_style", "create", "结算方式新增", A("AS018")),
                WriteRule("settle_style", "update", "结算方式修改", A("AS018")),
                WriteRule("settle_style", "delete", "结算方式删除", A("AS018")),
                WriteRule("user_define", "create", "自定义项档案新增", A("AS025A")),
                WriteRule("user_define", "update", "自定义项档案修改", A("AS025")),
                WriteRule("user_define", "delete", "自定义项档案删除", A("AS025D")),
                WriteRule("customer_inventory", "create", "客户存货对照新增", A("AS1204A"), contra),
                WriteRule("customer_inventory", "update", "客户存货对照修改", A("AS1204E"), contra),
                WriteRule("customer_inventory", "delete", "客户存货对照删除", A("AS1204D"), contra),
                // 总账基础档案：外币设置 GL_frmWBSZ 的 Add / Delete 是 AS028M（「外币编辑」，没有 Modify 按钮，修改按同一 id）；
                // 凭证类别 GL_frmPZLB 的 Add / Modify / Delete 都是 AS026。会计科目仍只读。
                WriteRule("currency", "create", "币种新增", A("AS028M")),
                WriteRule("currency", "update", "币种修改", A("AS028M")),
                WriteRule("currency", "delete", "币种删除", A("AS028M")),
                WriteRule("voucher_sign", "create", "凭证类别新增", A("AS026")),
                WriteRule("voucher_sign", "update", "凭证类别修改", A("AS026")),
                WriteRule("voucher_sign", "delete", "凭证类别删除", A("AS026"))
            };
        }

        static PermRule WriteRule(string name, string op, string title, string[] auths, params PermObj[] objs)
        {
            return R(ArcGuard.RuleKey(name, op), title, auths, objs);
        }

        // 档案：Q 结尾是查询，不带 Q 的是列表修改；任一即可。分类、计量单位、结算方式、币种、银行不受记录级控制。
        static PermRule[] ArchiveBaseRules()
        {
            return new PermRule[]
            {
                Arc("customer", "客户档案", A("AS011Q", "AS011"), PermObj.H(PermObj.Customer, "cCusCode")),
                Arc("vendor", "供应商档案", A("AS005Q", "AS005"), PermObj.H(PermObj.Vendor, "cVenCode")),
                Arc("inventory", "存货档案", A("AS009Q", "AS009"), PermObj.H(PermObj.Inventory, "cInvCode")),
                Arc("inventory_class", "存货分类", A("AS008Q", "AS008")),
                Arc("warehouse", "仓库档案", A("AS002Q", "AS002"), PermObj.H(PermObj.Warehouse, "cWhCode")),
                Arc("department", "部门档案", A("AS021Q", "AS021"), PermObj.H(PermObj.Department, "cDepCode")),
                Arc("person", "人员档案", A("AS020Q", "AS020"), PermObj.H(PermObj.Person, "cPsn_Num")),
                Arc("customer_class", "客户分类", A("AS010Q", "AS010")),
                // 供应商分类：「供应商分类查询」AS004Q、「供应商分类编辑」AS004（按 U8 授权目录核对）。
                Arc("vendor_class", "供应商分类", A("AS004Q", "AS004")),
                // 会计科目没有专门的查询 id：任一总账功能即可。
                // 科目的数据权限只用于总账余额类报表（见 OtherRules），科目档案不按科目过滤。
                Arc("account", "会计科目", A("GL%")),
                Arc("unit", "计量单位", A("AS032Q", "AS032M")),
                // 计量单位组与计量单位同一表单（「计量单位查询」AS032Q、「计量单位编辑」AS032M，按 U8 授权目录核对）。
                Arc("unit_group", "计量单位组", A("AS032Q", "AS032M")),
                Arc("settle_style", "结算方式", A("AS018Q", "AS018")),
                // 凭证类别表单 GL_frmPZLB 的按钮都是 AS026（UFMeta 里没有 AS026Q）。
                Arc("voucher_sign", "凭证类别", A("AS026"), PermObj.H(PermObj.Sign, "csign")),
                // 币种：外币设置表单 GL_frmWBSZ 的按钮是 AS028M（UFMeta AA_FormButtonAuths；AS027* 在 UFMeta 里不存在）。
                Arc("currency", "币种", A("AS028M")),
                // 本单位开户银行：「本单位开户银行查询」AS013Q、「本单位开户银行编辑」AS013（按 U8 授权目录核对；
                // 原先登记的 AS016Q 是「收发类别查询」）。
                Arc("bank", "开户银行", A("AS013Q", "AS013")),
                Arc("project", "项目档案", A("AS029Q", "AS029M"), PermObj.P("cls", "citemcode"))
            };
        }

        // 只读档案。自定义项的 AS025Q / AS025 见于 U8 自定义项表单（UFMeta 的 AA_FormButtons_base）；
        // 客户存货对照的查询 id 是 AS1204Q（在授权表里核对，上级 AS1204 不进授权表），另收修改 AS1204E；
        // 客户收货地址是客户档案的子页，用客户档案的 id。
        // 采购类型、销售类型、地区分类的 id 以 U8 授权目录为准。
        // 收发类别、采购类型、销售类型按各自的数据权限对象过滤（同凭证类别）；货位不按仓库过滤（货位对象不展开）。
        static PermRule[] ArchiveRoRules()
        {
            return new PermRule[]
            {
                // 货位：UFMeta 的货位表单 Archives_IPosition_List 按钮不带 id，原先的 AS003 / AS003Q 是付款条件（核对）。
                // 按 U8 授权目录更正为「货位档案查询」AS030Q、「货位档案编辑」AS030（原先的 AS014Q / AS014 是「成套件」）。
                Arc("position", "货位档案", A("AS030Q", "AS030")),
                // 收发类别：「收发类别查询」AS016Q、「收发类别编辑」AS016（按 U8 授权目录更正；原先的 AS006Q / AS006 是「发运方式」）。
                Arc("rd_style", "收发类别", A("AS016Q", "AS016"), PermObj.H(PermObj.RdStyle, "cRdCode")),
                Arc("purchase_type", "采购类型", A("AS007Q", "AS007"), PermObj.H(PermObj.PurchaseType, "cPTCode")),
                // 销售类型、地区分类只有列表 id AS012 / AS001（核对：AS012Q / AS001Q 从未出现在授权表里，已去掉）。
                Arc("sale_type", "销售类型", A("AS012"), PermObj.H(PermObj.SaleType, "cSTCode")),
                Arc("district_class", "地区分类", A("AS001")),
                // 行业分类：「行业分类查询」AS050Q、「行业分类编辑」AS050（按 U8 授权目录更正；原先的 AS013Q / AS013 是本单位开户银行）。
                Arc("trade_class", "行业分类", A("AS050Q", "AS050")),
                // 银行档案：UFMeta / 授权表里没找到对应的 id（AS015 / AS015Q 不存在；AS013 是本单位开户银行，不能代用）。
                // 与操作员、角色同一口径，只认账套主管（admin），不按记录过滤。注意：客户、供应商银行账户和本单位开户银行
                // 的写入要填 AA_Bank 的所属银行编码（bank_code），非主管查不到银行档案，只能向主管要编码。
                Arc("aa_bank", "银行档案", A("admin")),
                // 原因码（ArcReason）：U8 桌面配置里原因码档案的 cAuthId 为空、UFMeta 里也没有它的表单按钮，没有功能 id 可查；
                // U8 自己不按功能控制它，这里同样登录即可、不按记录过滤。写入不登记，交给 U8。
                Arc("reason", "原因码档案", A()),
                // 与 ArcPair 的 Obj1 / Obj2 一致：get 按编码的客户段、存货段分别判断。
                Arc("customer_address", "客户收货地址", A("AS011Q", "AS011"), PermObj.H(PermObj.Customer, "cCusCode")),
                Arc("user_define", "自定义项档案", A("AS025Q", "AS025")),
                Arc("customer_inventory", "客户存货对照", A("AS1204Q", "AS1204E"),
                    PermObj.H(PermObj.Customer, "cCusCode"), PermObj.H(PermObj.Inventory, "cInvCode")),
                // 汇率在 U8 外币设置里维护，与币种同一表单，用「外币编辑」AS028M（按 U8 授权目录核对）；不受记录级控制。
                Arc("exchange_rate", "汇率", A("AS028M")),
                // 固定资产卡片：卡片管理列表 FA_Cards 的「打开」（K106）是 FA1501，「修改」（K122）是 FA1505（UFMeta AA_FormButtonAuths），
                // 有其一即可。部门开关打开时按卡片使用部门过滤（ArcFa 调 ReportsFaPerm，不登记在规则里）；类别不过滤。
                Arc("fa_card", "固定资产卡片", A("FA1501", "FA1505")),
                // 操作员、角色（ArcUa）：U8 在系统管理里维护，账套库的 UFMeta 没有对应的功能 id 可查。只认账套主管
                // （admin，PermContext.Supervisor 直接放行），不按记录过滤。未覆盖：要放宽时换成企业应用平台里的权限类功能 id。
                Arc("operator", "操作员", A("admin")),
                Arc("role", "角色", A("admin"))
            };
        }

        static PermRule R(string key, string title, string[] auths, params PermObj[] objs)
        {
            return new PermRule(key, title, auths, objs);
        }

        // 总账凭证、现存量、报表、审批任务。
        // 已按 U8 授权目录核对：余额表 GL030301、明细账 GL0305、结账 GL1512、应收 / 应付总账表 AR060201_01 / AP060201_01、
        // 应收 / 应付明细账 AR060202_01 / AP060202_01、对账单 AR060203_01 / AP060203_01、账龄分析 AR060301 / AP060301
        // （另收 UAP 报表视图的「查询:应收账龄分析 / 应付账龄分析」）。
        // 科目（code）只在总账选项「明细账查询权限控制到科目」（AccInformation GL bQryCtlSubj）打开时过滤，见 PermContext.Controls。
        // 账龄分析的 UAP 报表视图「查询」id（UA_Auth 实查）。
        const string ArAgingView = "AR[__]bdfd5d21-763b-4d7b-a6ae-eb3abb026f91_001_01";
        const string ApAgingView = "AP[__]5d20e375-da98-4d59-a5a5-345d9123f2d0_002_01";

        static PermRule[] OtherRules()
        {
            PermObj[] aux = new PermObj[]
            {
                PermObj.H(PermObj.Account, "ccode"), PermObj.Opt(PermObj.Customer, "ccus_id"),
                PermObj.Opt(PermObj.Vendor, "csup_id"), PermObj.Opt(PermObj.Department, "cdept_id"),
                PermObj.Opt(PermObj.Person, "cperson_id"), OptItem("citem_class", "citem_id")
            };
            return new PermRule[]
            {
                // 凭证查询（gl/vouchers/load、list、digest、attachments/list）：按科目过滤，只在总账选项「明细账查询权限
                // 控制到科目」（bQryCtlSubj）打开时生效（PermContext.Controls）。U8 客户端查询凭证是否逐张按科目控制未核对，
                // 取较严的口径：一张凭证的全部分录科目都有查询权限才可见（PermSql.AppendGlVoucher、PermCheck.AllLines）。
                // 查询他人凭证控制到操作员（bFindVouchCtrl）仍未实现。
                R("gl", "凭证查询", A("GL0202", "GL0201"), PermObj.Gl()),
                R("stock", "现存量查询", A("ST020107_01"),
                    PermObj.H(PermObj.Warehouse, "cWhCode"), PermObj.H(PermObj.Inventory, "cInvCode")),
                // 没带 type 的待办只列操作员自己的任务，登录成功即可。
                R("tasks", "审批任务", A()),
                // 月末结账状态：总账「结账」GL1512、「反结账」GL1520，另收余额表、明细账的查询（已去掉「凭证整理」GL0202）。
                R("report:close_status", "月末结账状态", A("GL1512", "GL1520", "GL030301", "GL0305")),
                // 账套体检：账套主管（admin，PermContext.Supervisor 直接放行），或有总账「结账」GL1512、「凭证整理」GL0202 的操作员；
                // 不按记录过滤（只查配置和目录）。
                R(ReportsReadinessReq.RuleKey, "账套体检", A("admin", "Admin", "GL1512", "GL0202")),
                R("report:gl_balance", "科目余额表", A("GL030301", "GL0305", "GL030101"), PermObj.H(PermObj.Account, "ccode")),
                R("report:gl_aux_balance", "辅助余额表", A("GL030301", "GL0305"), aux),
                R("report:arap_balance:ar", "应收余额表", A("AR060201_01", "AR050104"), PermObj.H(PermObj.Customer, "cDwCode")),
                R("report:arap_balance:ap", "应付余额表", A("AP060201_01", "AP050104"), PermObj.H(PermObj.Vendor, "cDwCode")),
                R("report:arap_aging:ar", "应收账龄分析", A("AR060301", ArAgingView), PermObj.H(PermObj.Customer, "cDwCode")),
                R("report:arap_aging:ap", "应付账龄分析", A("AP060301", ApAgingView), PermObj.H(PermObj.Vendor, "cDwCode")),
                R("report:bom", "物料清单查询", A("BO01001Q"), PermObj.H(PermObj.Inventory, "InvCode")),
                // 明细账：总账明细账表单 GL_mxz 的「查询」是 GL0305（UFMeta AA_FormButtonAuths），受控对象同辅助余额表。
                // 往来明细账：「查询应收明细账」AR060202_01（应付 AP060202_01）为首选，另收对账单 AR060203_01、总账表 AR060201_01。
                R("report:gl_detail", "科目明细账", A("GL0305"), aux),
                R("report:arap_detail:ar", "应收明细账", A("AR060202_01", "AR060203_01", "AR060201_01"),
                    PermObj.H(PermObj.Customer, "cDwCode")),
                R("report:arap_detail:ap", "应付明细账", A("AP060202_01", "AP060203_01", "AP060201_01"),
                    PermObj.H(PermObj.Vendor, "cDwCode")),
                // 订单执行：闸门收销售订单或采购订单的查询 id（同 voucher:sale_order / voucher:purchase_order），
                // 处理函数再按请求的 type 要求该类型的读取规则，记录级条件同该类型的列表（ReportsOrderExec）。
                R("report:order_execution", "订单执行", A("SA03010104", "SA03010201", "PU0310")),
                // 单据追溯：登录成功即可进入；处理函数要求起点类型的读取规则，其余节点逐类按 voucher:<type> 判断（ReportsTrace）。
                R("report:doc_trace", "单据追溯", A()),
                // 库存报表，受控对象同现存量（仓库、存货，各报表的别名下都叫 cWhCode / cInvCode）。id 取自 UFMeta——
                // 库存台账表单 ST_Report_BCB 的打印 / 输出是 ST020102_03 / _04（AA_FormButtonAuths；上级菜单 ST020102 不进授权表，已去掉）；
                // 收发存汇总表是 UAP 报表 ST020301 视图 01（上级 ST020301 不进授权表，已去掉），货位汇总表 ST010812、批次存货汇总表 ST020305（UAP_ReportView_AuthId）。
                R("report:stock_ledger", "库存台账", A("ST020102_03", "ST020102_04"),
                    PermObj.H(PermObj.Warehouse, "cWhCode"), PermObj.H(PermObj.Inventory, "cInvCode")),
                R("report:stock_summary", "收发存汇总表", A("ST020301_01"),
                    PermObj.H(PermObj.Warehouse, "cWhCode"), PermObj.H(PermObj.Inventory, "cInvCode")),
                R("report:position_stock", "货位存量查询", A("ST020107_01", "ST010812_01"),
                    PermObj.H(PermObj.Warehouse, "cWhCode"), PermObj.H(PermObj.Inventory, "cInvCode")),
                R("report:batch_stock", "批次存货汇总表", A("ST020305_01", "ST020107_01"),
                    PermObj.H(PermObj.Warehouse, "cWhCode"), PermObj.H(PermObj.Inventory, "cInvCode")),
                // 信用余额表：UAP 报表 SA040410 视图 01（上级 SA040410 不进授权表，已去掉）。按客户过滤。
                R("report:customer_credit", "信用余额表", A("SA040410_01"), PermObj.H(PermObj.Customer, "cCusCode")),
                // 价格表：闸门收三种价格表的 id 之一，处理函数再按 kind 要求对应的 id（ReportsPrice）。
                // 客户、供应商按可空列处理（存货价格表、按客户分类的行没有客户）。
                R("report:price_list", "价格表", PriceAuths(),
                    PermObj.Opt(PermObj.Customer, "cCusCode"), PermObj.Opt(PermObj.Vendor, "cVenCode"),
                    PermObj.H(PermObj.Inventory, "cInvCode"))
            };
        }

        // 期初余额 opening_balance：闸门（report:opening_balance）收各模块 id 之一，处理函数再按 module / side / dim 要求
        // 对应的规则并按其对象过滤（ReportsOpening）。已按 U8 授权目录核对：总账期初余额表单 GL_frmQC 的按钮是 GL010303 / GL010304，
        // 应收 / 应付期初表单（AR_FrmQc* / AP_FrmQc*）的按钮是 AR0306 / AP0306（UFMeta AA_FormButtonAuths），
        // 库存期初收期初结存菜单 ST000101（卡片 0319 的按钮 ST000102–ST000106 见 voucher:stock_opening；核对，
        // 原先推断的 ST0102 不存在），另收现存量查询 ST020107_01。
        // 各模块另收本模块余额表的 id。
        static PermRule[] OpeningRules()
        {
            string[] st = A("ST000101", "ST020107_01");
            string[] ar = A("AR0306", "AR060201_01");
            string[] ap = A("AP0306", "AP060201_01");
            string[] gl = A("GL010303", "GL010304", "GL030301");
            PermObj[] aux = new PermObj[]
            {
                PermObj.H(PermObj.Account, "ccode"), PermObj.Opt(PermObj.Customer, "ccus_id"),
                PermObj.Opt(PermObj.Vendor, "csup_id"), PermObj.Opt(PermObj.Department, "cdept_id"),
                PermObj.Opt(PermObj.Person, "cperson_id"), OptItem("citem_class", "citem_id")
            };
            System.Collections.Generic.List<string> all = new System.Collections.Generic.List<string>();
            all.AddRange(st);
            all.AddRange(ar);
            all.AddRange(ap);
            all.AddRange(gl);
            const string key = "report:opening_balance";
            return new PermRule[]
            {
                R(key, "期初余额", all.ToArray()),
                R(key + ":stock", "库存期初", st,
                    PermObj.H(PermObj.Warehouse, "cWhCode"), PermObj.H(PermObj.Inventory, "cInvCode")),
                R(key + ":ar", "应收期初", ar, PermObj.H(PermObj.Customer, "cDwCode")),
                R(key + ":ap", "应付期初", ap, PermObj.H(PermObj.Vendor, "cDwCode")),
                R(key + ":gl", "总账期初余额", gl, PermObj.H(PermObj.Account, "ccode")),
                R(key + ":gl_aux", "总账辅助期初余额", gl, aux)
            };
        }

        static string[] PriceAuths()
        {
            string[] a = ReportsPrice.CustomerAuths;
            string[] b = ReportsPrice.InventoryAuths;
            string[] c = ReportsPrice.VendorAuths;
            string[] all = new string[a.Length + b.Length + c.Length];
            a.CopyTo(all, 0);
            b.CopyTo(all, a.Length);
            c.CopyTo(all, a.Length + b.Length);
            return all;
        }

        // 项目在单据、辅助账上常为空：空值不算越权。
        static PermObj OptItem(string classColumn, string codeColumn)
        {
            PermObj item = PermObj.P(classColumn, codeColumn);
            item.Optional = true;
            return item;
        }

        // 物料清单（type=bom）：读取按卡片 BO_Voucher_BO01001 的 tlbFilter（BO01001Q，与报表 bom 相同）；
        // 写规则取同一卡片的 tlbAdd / tlbModify / tlbDelete / tlbConfirm / tlbUnConfirm（UFMeta AA_FormButtonAuths）。
        // 数据权限按母件存货：读取、列表经视图 v_BOM_BomParent（BomId → InvCode），写路由按请求或表头的母件（BomRoutes.ParentRows）。
        static PermRule[] BomRules()
        {
            PermObj parent = PermObj.H(PermObj.Inventory, "inv_code");
            return new PermRule[]
            {
                V(BomRoutes.KindName, "物料清单", A("BO01001Q"),
                    PermObj.B(PermObj.Inventory, "v_BOM_BomParent", "BomId", "BomId", "InvCode")),
                R(BomRoutes.CreateRule, "物料清单新增", A("BO01001N"), parent),
                R(BomRoutes.UpdateRule, "物料清单修改", A("BO01001M"), parent),
                R(BomRoutes.DeleteRule, "物料清单删除", A("BO01001D"), parent),
                R(BomRoutes.VerifyRule, "物料清单审核", A("BO01001A"), parent),
                R(BomRoutes.UnverifyRule, "物料清单弃审", A("BO01001UA"), parent)
            };
        }
    }
}
