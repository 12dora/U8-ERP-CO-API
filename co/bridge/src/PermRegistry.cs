using System;
using System.Collections.Generic;

namespace U8Co
{
    // 读路由 → 功能权限 id（任一即可）+ 数据权限对象与列。唯一的一份登记表：commander 实测后只改这里。
    // 全部 id 已按 U8 授权目录（UFSystem 的 UA_Auth 名称与 UA_Menu 菜单）核对含义；仍未在 U8 客户端逐个授权点验。
    // 读路由没有登记时一律拒绝（PermGate 403），新增读路由必须在这里加一行。
    internal static partial class PermRegistry
    {
        const string Load = "/u8co/v1/vouchers/load";
        const string WfRoot = "/u8co/v1/workflow/";

        static readonly Dictionary<string, PermRule> Map = Build();

        // 读路由（要做功能权限检查的路由）。写路由、login-check、meta、health 不在这里。报表整组都是读。
        static readonly HashSet<string> ReadPaths = new HashSet<string>(StringComparer.Ordinal)
        {
            Load, Requests.ListPath, Requests.StockPath, WfRoot + "state", WfRoot + "history", WfRoot + "tasks",
            Requests.GlRoot + "load", Requests.GlRoot + "list", Requests.ArcRoot + "get", Requests.ArcRoot + "list",
            // 凭证附件（权限同凭证读取 "gl"）、单据附件（同该类型的读取 voucher:<type>）。
            GlAttach.Path, VoucherAttach.Path,
            // 名称解析：按每项的档案逐项查权限（PermRegistryResolve，PermGate 整体放过）。
            ArcResolve.Path,
            // 单据搜索、单据批量读取（同该类型的 voucher:<type>）、档案批量读取（同 archives/get 的 archive:<档案>）。
            VoucherSearch.Path, LoadMany.Path, ArcGetMany.Path,
            // 凭证摘要（GlDigest，事件源）：权限同凭证查询 "gl"。
            GlDigest.Path,
            // 单张票据读取（NotesRead）：同票据列表的 voucher:ar_note / voucher:ap_note。
            NotesReadReq.Path,
            // 应收 / 应付处理记录（ArapProcList，事件源）：按 flag 分 read:arap:process_list:ar|ap。
            ArapProcListReq.Path,
            // 权限快照、权限评估（PermRegistryPerm.cs）：登录即可；评估另在处理函数里查 permEvaluateOperators。
            PermSnapshot.Path, PermEvaluate.Path
        };

        public static bool IsRead(string path)
        {
            if (path == null)
            {
                return false;
            }
            return ReadPaths.Contains(path) || path.StartsWith(Requests.ReportRoot, StringComparison.Ordinal);
        }

        // 找不到返回 null（调用方按拒绝处理）。
        public static PermRule Find(WorkItem item)
        {
            string key = KeyOf(item);
            PermRule rule;
            if (key != null && Map.TryGetValue(key, out rule))
            {
                return rule;
            }
            return null;
        }

        public static PermRule ForKey(string key)
        {
            PermRule rule;
            if (key != null && Map.TryGetValue(key, out rule))
            {
                return rule;
            }
            return null;
        }

        public static IEnumerable<string> Keys()
        {
            return Map.Keys;
        }

        // 按请求里的单据类型取 voucher:<type> 规则的路由（没带类型的列表按 body.type）。
        static readonly HashSet<string> TypedPaths = new HashSet<string>(StringComparer.Ordinal)
        {
            Load, Requests.ListPath, WfRoot + "state", WfRoot + "history", VoucherAttach.Path,
            // 单据搜索、单据批量读取。
            VoucherSearch.Path, LoadMany.Path
        };

        static string KeyOf(WorkItem item)
        {
            if (item == null || item.Path == null)
            {
                return null;
            }
            string path = item.Path;
            if (TypedPaths.Contains(path))
            {
                return item.Type == null ? ListType(item) : "voucher:" + item.Type.Name;
            }
            if (path == WfRoot + "tasks")
            {
                return item.Type == null ? "tasks" : "voucher:" + item.Type.Name;
            }
            return OtherKey(item, path);
        }

        static string OtherKey(WorkItem item, string path)
        {
            if (path == Requests.StockPath)
            {
                return "stock";
            }
            if (path.StartsWith(Requests.GlRoot, StringComparison.Ordinal))
            {
                return "gl";
            }
            if (path.StartsWith(Requests.ArcRoot, StringComparison.Ordinal))
            {
                return "archive:" + BodyText(item, "archive");
            }
            // 权限快照、权限评估：perm:snapshot / perm:evaluate（PermRegistryPerm.cs）。
            string perm = PermKey(path);
            if (perm != null)
            {
                return perm;
            }
            // 应收 / 应付处理记录：按 body.flag（ArapProcListReq.RuleKey）。
            if (ArapProcListReq.IsPath(path))
            {
                return ArapProcListReq.RuleKey(item);
            }
            // 单张票据读取：按 body.type（NotesReadReq.RuleKey）。
            return NotesReadReq.IsPath(path) ? NotesReadReq.RuleKey(item) : ReportKey(item);
        }

        // vouchers/list 的类型在请求体的 type 里（登录前已校验）。
        static string ListType(WorkItem item)
        {
            string type = BodyText(item, "type");
            return type.Length == 0 ? null : "voucher:" + type;
        }

        // 往来报表按 side 分应收 / 应付两条。
        static string ReportKey(WorkItem item)
        {
            if (!item.Path.StartsWith(Requests.ReportRoot, StringComparison.Ordinal))
            {
                return null;
            }
            string name = item.Path.Substring(Requests.ReportRoot.Length);
            if (name == "arap_balance" || name == "arap_aging" || name == "arap_detail")
            {
                return "report:" + name + ":" + (BodyText(item, "side") == "ap" ? "ap" : "ar");
            }
            // 核销记录按 flag（AR / AP）分两条。
            if (ReportsArapWriteoffReq.Owns(name))
            {
                return ReportsArapWriteoffReq.RuleKey(item.Body);
            }
            // 经营管理往来账期按 side 分两条。
            if (ReportsMgmtArapReq.Owns(name))
            {
                return ReportsMgmtArapReq.RuleKey(item.Body);
            }
            return "report:" + name;
        }

        static string BodyText(WorkItem item, string key)
        {
            object raw = item.Body == null ? null : Requests.Field(item.Body, key);
            string text = raw as string;
            return text == null ? "" : text;
        }

        static Dictionary<string, PermRule> Build()
        {
            Dictionary<string, PermRule> map = new Dictionary<string, PermRule>(StringComparer.Ordinal);
            AddAll(map, SaRules());
            AddAll(map, PuRules());
            AddAll(map, StRules());
            AddAll(map, MoQmRules());
            AddAll(map, BomRules());
            AddAll(map, ArRules());
            AddAll(map, ArchiveRules());
            AddAll(map, OtherRules());
            // 期初余额报表 opening_balance（PermRegistryMore.OpeningRules）。
            AddAll(map, OpeningRules());
            // 销售订单 / 采购订单锁定、解锁（PermRegistryLock）。
            AddAll(map, LockRules());
            // 形态转换单、调拨申请单、盘点单（PermRegistryStMisc.cs）。
            AddAll(map, StMiscRules());
            // 质量单据新增、删除、审核（PermRegistryQm.cs）。
            AddAll(map, QmWriteRules());
            // 客户、供应商的银行账户和联系人（PermRegistryPartner.cs）。
            AddAll(map, PartnerRules());
            // 采购发票应付审核、销售发票应收审核及弃审（PermRegistryArap.cs）。
            AddAll(map, ArapAuditRules());
            // 应收 / 应付核销（PermRegistryWriteoff.cs）。
            AddAll(map, WriteoffRules());
            // 核销记录查询 reports/arap_writeoffs（PermRegistryWriteoff.cs）。
            AddAll(map, WriteoffListRules());
            // 取消应收冲应付 / 应付冲应收 / 并账（ArapProcCancelReq.cs）。
            AddAll(map, ProcCancelRules());
            // 应收 / 应付并账（PermRegistryMerge.cs）。
            AddAll(map, MergeRules());
            // 应收冲应付 / 应付冲应收（PermRegistryTransfer.cs）。
            AddAll(map, TransferRules());
            // 红票对冲（ArapRedReq.cs）。
            AddAll(map, RedOffsetRules());
            // 票据处理（NotesProcReq.cs）。
            AddAll(map, NotesProcRules());
            // 票据登记、删除（NotesRegReq.cs）。
            AddAll(map, NotesRegRules());
            // 汇兑损益、取消汇兑损益（PermRegistryExGain.cs）。
            AddAll(map, ExGainRules());
            // 坏账发生、坏账收回、计提坏账准备（ArapBadReq.cs）。
            AddAll(map, BadDebtRules());
            // 总账记账（PermRegistryGl.cs）。
            AddAll(map, GlPostRules());
            // 应收 / 应付制单、取消制单（PermRegistryArapVoucher.cs）。
            AddAll(map, ArapVoucherRules());
            // 期初记账 / 取消记账（PermRegistryOpening.cs）。
            AddAll(map, OpeningPostRules());
            // 月末结账 / 取消结账（PermRegistryPeriod.cs）。
            AddAll(map, PeriodCloseRules());
            // 存货核算记账、期末处理（PermRegistryIa.cs）。
            AddAll(map, IaRules());
            // 应收 / 应付票据的列表与单张读取（PermRegistryNotesRead.cs）。
            AddAll(map, NoteReadRules());
            // 应收 / 应付处理记录 arap/process/list（ArapProcListReq.cs）。
            AddAll(map, ProcListRules());
            // 出入库调整单、存货调价单、固定资产变动单与折旧报表（PermRegistryIaSaFa.cs），只读。
            AddAll(map, IaSaFaRules());
            // 采购结算单自动结算、删除（PermRegistryPuSettle.cs）。
            AddAll(map, PuSettleWriteRules());
            // 退货申请单（PermRegistryReturnsApply.cs），只读。
            AddAll(map, ReturnsApplyRules());
            // 经营管理报表（总账口径）mgmt/pnl、mgmt/meta、mgmt/cash_stock（PermRegistryMgmtGl.cs），只读。
            AddAll(map, MgmtGlRules());
            // 经营管理销售分析、往来账期 mgmt/sales、mgmt/arap_terms（PermRegistryMgmtSalesArap.cs），只读。
            AddAll(map, MgmtSalesArapRules());
            // 权限快照、权限评估（PermRegistryPerm.cs），只读。
            AddAll(map, PermSnapshotRules());
            // 固定资产卡片新增 / 撤销、变动单新增、设备台账（PermRegistryFa.cs）。
            AddAll(map, FaEqRules());
            return map;
        }

        static void AddAll(Dictionary<string, PermRule> map, PermRule[] rules)
        {
            for (int i = 0; i < rules.Length; i++)
            {
                map.Add(rules[i].Key, rules[i]);
            }
        }

        static PermRule V(string type, string title, string[] auths, params PermObj[] objs)
        {
            return new PermRule("voucher:" + type, title + "查询", auths, objs);
        }

        static string[] A(params string[] ids)
        {
            return ids;
        }

        static PermRule[] SaRules()
        {
            return new PermRule[]
            {
                V("sale_order", "销售订单", A("SA03010104", "SA03010201"),
                    PermObj.H(PermObj.Customer, "cCusCode"), PermObj.H(PermObj.Department, "cDepCode"),
                    PermObj.H(PermObj.Person, "cPersonCode"), PermObj.H(PermObj.SaleType, "cSTCode"),
                    PermObj.B(PermObj.Inventory, "SO_SODetails", "ID", "ID", "cInvCode")),
                V("dispatch", "发货单", A("SA03020104", "SA03020301"),
                    PermObj.H(PermObj.Customer, "cCusCode"), PermObj.H(PermObj.Department, "cDepCode"),
                    PermObj.H(PermObj.Person, "cPersonCode"), PermObj.H(PermObj.SaleType, "cSTCode"),
                    PermObj.B(PermObj.Inventory, "DispatchLists", "DLID", "DLID", "cInvCode"),
                    PermObj.B(PermObj.Warehouse, "DispatchLists", "DLID", "DLID", "cWhCode")),
                // 销售专用发票、普通发票的菜单 SA03030201 / SA03030301，卡片 SAM030401 / SAM030402 的增加、修改
                // SA03030202 / SA03030302，或销售发票的下查 SA03030101（核对；原先的 SA03030204 / SA03030304 是作废 / 弃废）。
                V("sale_invoice", "销售发票", A("SA03030201", "SA03030301", "SA03030202", "SA03030302", "SA03030101"),
                    PermObj.H(PermObj.Customer, "cCusCode"), PermObj.H(PermObj.Department, "cDepCode"),
                    PermObj.H(PermObj.Person, "cPersonCode"), PermObj.H(PermObj.SaleType, "cSTCode"),
                    PermObj.B(PermObj.Inventory, "SaleBillVouchs", "SBVID", "SBVID", "cInvCode"),
                    PermObj.B(PermObj.Warehouse, "SaleBillVouchs", "SBVID", "SBVID", "cWhCode")),
                // 「退货单查询」SA03020204；U8 的「发货单列表」SA03020301 也列退货单（按 U8 授权目录核对）。
                V("sale_return", "退货单", A("SA03020204", "SA03020301"),
                    PermObj.H(PermObj.Customer, "cCusCode"), PermObj.H(PermObj.Department, "cDepCode"),
                    PermObj.H(PermObj.Person, "cPersonCode"), PermObj.H(PermObj.SaleType, "cSTCode"),
                    PermObj.B(PermObj.Inventory, "DispatchLists", "DLID", "DLID", "cInvCode"),
                    PermObj.B(PermObj.Warehouse, "DispatchLists", "DLID", "DLID", "cWhCode"))
            };
        }

        // 采购类型、业务员、到货单表体仓库在 U8 里常为空，按可空列处理（未经实测）。
        static PermRule[] PuRules()
        {
            return new PermRule[]
            {
                V("purchase_order", "采购订单", A("PU0310"),
                    PermObj.H(PermObj.Vendor, "cVenCode"), PermObj.H(PermObj.Department, "cDepCode"),
                    PermObj.Opt(PermObj.Person, "cPersonCode"), PermObj.Opt(PermObj.PurchaseType, "cPTCode"),
                    PermObj.B(PermObj.Inventory, "PO_Podetails", "POID", "POID", "cInvCode")),
                V("arrival", "到货单", A("PU04200107"),
                    PermObj.H(PermObj.Vendor, "cVenCode"), PermObj.H(PermObj.Department, "cDepCode"),
                    PermObj.Opt(PermObj.Person, "cPersonCode"), PermObj.Opt(PermObj.PurchaseType, "cPTCode"),
                    PermObj.B(PermObj.Inventory, "PU_ArrivalVouchs", "ID", "ID", "cInvCode"),
                    PermObj.BOpt(PermObj.Warehouse, "PU_ArrivalVouchs", "ID", "ID", "cWhCode")),
                V("purchase_invoice", "采购发票", A("PU04300108", "PU04300208"),
                    PermObj.H(PermObj.Vendor, "cVenCode"), PermObj.H(PermObj.Department, "cDepCode"),
                    PermObj.Opt(PermObj.Person, "cPersonCode"), PermObj.Opt(PermObj.PurchaseType, "cPTCode"),
                    PermObj.B(PermObj.Inventory, "PurBillVouchs", "PBVID", "PBVID", "cInvCode")),
                // 采购退货单卡片（PU_FrmS_Voucher_26_1r）的 QueryConfirm 是「退货单查审」PU04200207；到货单列表也列退货单，按「到货单查审」PU04200107 认（核对）。
                V("purchase_return", "采购退货单", A("PU04200207", "PU04200107"),
                    PermObj.H(PermObj.Vendor, "cVenCode"), PermObj.H(PermObj.Department, "cDepCode"),
                    PermObj.Opt(PermObj.Person, "cPersonCode"), PermObj.Opt(PermObj.PurchaseType, "cPTCode"),
                    PermObj.B(PermObj.Inventory, "PU_ArrivalVouchs", "ID", "ID", "cInvCode"),
                    PermObj.BOpt(PermObj.Warehouse, "PU_ArrivalVouchs", "ID", "ID", "cWhCode")),
                // 请购单：PU_frmVoucherList_27 的 QueryConfirm。部门、业务员可空，建议供应商在表体上可空（未经实测）。
                V("purchase_requisition", "请购单", A("PU04100105"),
                    PermObj.Opt(PermObj.Department, "cDepCode"), PermObj.Opt(PermObj.Person, "cPersonCode"),
                    PermObj.Opt(PermObj.PurchaseType, "cPTCode"),
                    PermObj.B(PermObj.Inventory, "PU_AppVouchs", "ID", "ID", "cInvCode"),
                    PermObj.BOpt(PermObj.Vendor, "PU_AppVouchs", "ID", "ID", "cVenCode")),
                // 采购结算单（只读）：UA_Auth「结算单列表查询」PU040305（结算单没有单独的查询 id，核对）。
                // 部门、业务员、采购类型在结算单上常为空，按可空列；表体仓库可空。
                V(PuSettleRead.KindName, "采购结算单", A("PU040305"),
                    PermObj.H(PermObj.Vendor, "cVenCode"), PermObj.Opt(PermObj.Department, "cDepCode"),
                    PermObj.Opt(PermObj.Person, "cPersonCode"), PermObj.Opt(PermObj.PurchaseType, "cPTCode"),
                    PermObj.B(PermObj.Inventory, "PurSettleVouchs", "PSVID", "PSVID", "cInvCode"),
                    PermObj.BOpt(PermObj.Warehouse, "PurSettleVouchs", "PSVID", "PSVID", "cWhCode"))
            };
        }

        static PermRule[] StRules()
        {
            return new PermRule[]
            {
                Rd("purchase_in", "采购入库单", "ASM0101", "rdrecords01", false, true),
                Rd("other_in", "其他入库单", "ASM0501", "rdrecords08", false, false),
                Rd("other_out", "其他出库单", "ASM0601", "rdrecords09", false, false),
                Rd("product_in", "产成品入库单", "ASM0301", "rdrecords10", false, false),
                Rd("material_out", "材料出库单", "ASM0401", "rdrecords11", false, false),
                Rd("sale_out", "销售出库单", "ASM0201", "rdrecords32", true, false),
                // 期初结存：期初结存菜单（浏览）ST000101，或卡片 0319 的修改、审核、弃审、取数、删除 ST000102–ST000106，任一即可。
                // 期初单表头部门可能为空，部门、业务员按可空列；不用 Rd()，它要求部门必有。
                V(StockOpening.KindName, "期初结存", A("ST000101", "ST000102", "ST000103", "ST000104", "ST000105", "ST000106"),
                    PermObj.H(PermObj.Warehouse, "cWhCode"), PermObj.Opt(PermObj.Department, "cDepCode"),
                    PermObj.Opt(PermObj.Person, "cPersonCode"),
                    PermObj.B(PermObj.Inventory, "rdrecords34", "ID", "ID", "cInvCode")),
                // 「调拨单查询」ST010101（按 U8 授权目录核对）。调出、调入两个仓库都要有权限。
                V("transfer", "调拨单", A("ST010101"),
                    PermObj.H(PermObj.Warehouse, "cOWhCode"), PermObj.H(PermObj.Warehouse, "cIWhCode"),
                    PermObj.H(PermObj.Department, "cODepCode"), PermObj.Opt(PermObj.Person, "cPersonCode"),
                    PermObj.B(PermObj.Inventory, "TransVouchs", "ID", "ID", "cInvCode"))
            };
        }

        // 收发记录：客户只在销售出库上必有、供应商只在采购入库上必有，其余单据上可空。收发类别按可空列处理（未经实测）。
        static PermRule Rd(string type, string title, string auth, string body, bool cus, bool ven)
        {
            PermObj customer = cus ? PermObj.H(PermObj.Customer, "cCusCode") : PermObj.Opt(PermObj.Customer, "cCusCode");
            PermObj vendor = ven ? PermObj.H(PermObj.Vendor, "cVenCode") : PermObj.Opt(PermObj.Vendor, "cVenCode");
            return V(type, title, A(auth), customer, vendor,
                PermObj.H(PermObj.Warehouse, "cWhCode"), PermObj.H(PermObj.Department, "cDepCode"),
                PermObj.Opt(PermObj.Person, "cPersonCode"), PermObj.Opt(PermObj.RdStyle, "cRdCode"),
                PermObj.B(PermObj.Inventory, body, "ID", "ID", "cInvCode"));
        }
    }
}
