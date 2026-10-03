using System;

namespace U8Co
{
    // 读线程池的路由名单。只收处理函数只跑 SQL、不碰 ctx.Session 和 U8 COM 的路由；
    // 需要登录对象上别的东西（userToken、cEmployeeId、CO 组件）的留在写线程池。
    // 加一条路由就是加一行：{ 路径, 单据类型 }，类型写 "*" 表示该路由的所有类型（含不带类型的请求）。
    internal static class RouteClass
    {
        const string Any = "*";
        const string LoadPath = "/u8co/v1/vouchers/load";

        static readonly string[][] SqlReads = new string[][]
        {
            // VoucherRead.Load → SqlRead.Load：表头、表体、HeadRow / 生产订单子件，全是 SQL。
            new string[] { "/u8co/v1/vouchers/load", "purchase_invoice" },
            new string[] { "/u8co/v1/vouchers/load", "production_order" },
            // VoucherRead.Load → BomRead.Load：物料清单表头、子件，全是 SQL。
            new string[] { "/u8co/v1/vouchers/load", "bom" },
            // VoucherRead.Load → PuSettleRead.Load：采购结算单表头、表体与入库 / 发票对照，全是 SQL。
            new string[] { "/u8co/v1/vouchers/load", PuSettleRead.KindName },
            // VoucherRead.Load → IaAdjustRead / InvPriceAdjustRead：出入库调整单、存货调价单的表头和表体，全是 SQL。
            new string[] { "/u8co/v1/vouchers/load", IaAdjustRead.KindName },
            new string[] { "/u8co/v1/vouchers/load", InvPriceAdjustRead.KindName },
            // VoucherRead.Load → ReturnsApplyRead：退货申请单表头和表体，全是 SQL。
            new string[] { "/u8co/v1/vouchers/load", ReturnsApplyRead.KindName },
            // VoucherRead.Load → PositionAdjust.Load：货位调整单表头、表体、货位台账，全是 SQL。
            new string[] { "/u8co/v1/vouchers/load", PositionAdjust.KindName },
            // VoucherRead.Load → QmRead.Load + WfState.Describe：SQL。没有登录时不查弃审次数，它不在响应里。
            new string[] { "/u8co/v1/vouchers/load", "qm_incoming_check" },
            new string[] { "/u8co/v1/vouchers/load", "qm_product_check" },
            new string[] { "/u8co/v1/vouchers/load", "qm_incoming_reject" },
            new string[] { "/u8co/v1/vouchers/load", "qm_product_reject" },
            // VoucherRead.Load → QmRead.Load → QmInspect.Load：报检单只查表，没有审批状态。
            new string[] { "/u8co/v1/vouchers/load", "qm_incoming_inspect" },
            new string[] { "/u8co/v1/vouchers/load", "qm_product_inspect" },
            // 其他报检单（QmInspect）、其他检验单（QmRead，不接审批流，不查 wf）：SQL。
            new string[] { "/u8co/v1/vouchers/load", "qm_other_inspect" },
            new string[] { "/u8co/v1/vouchers/load", "qm_other_check" },
            // Workflow.State / History → WfState.Load / History：SQL，同上。
            new string[] { "/u8co/v1/workflow/state", Any },
            new string[] { "/u8co/v1/workflow/history", Any },
            // 总账凭证读取与列表（GlRoutes）、档案读取与列表（ArcRoutes）、单据列表与现存量（ListRoutes）。
            // 这些处理函数只能用 ctx.Conn 和 ctx.OperatorName，不能碰 ctx.Session。
            new string[] { "/u8co/v1/gl/vouchers/load", Any },
            new string[] { "/u8co/v1/gl/vouchers/list", Any },
            // 凭证摘要（GlDigest，事件源）：只用 ctx.Conn、登录日期和权限快照。
            new string[] { GlDigest.Path, Any },
            new string[] { "/u8co/v1/archives/get", Any },
            new string[] { "/u8co/v1/archives/list", Any },
            new string[] { "/u8co/v1/vouchers/list", Any },
            new string[] { "/u8co/v1/stock/current", Any },
            // 只读报表（Reports*.cs）：全是 SQL，只用 ctx.Conn 和登录日期。
            new string[] { "/u8co/v1/reports/close_status", Any },
            new string[] { "/u8co/v1/reports/gl_balance", Any },
            new string[] { "/u8co/v1/reports/gl_aux_balance", Any },
            new string[] { "/u8co/v1/reports/arap_balance", Any },
            new string[] { "/u8co/v1/reports/arap_aging", Any },
            new string[] { "/u8co/v1/reports/bom", Any },
            new string[] { "/u8co/v1/reports/arap_detail", Any },
            new string[] { "/u8co/v1/reports/gl_detail", Any },
            new string[] { "/u8co/v1/reports/order_execution", Any },
            new string[] { "/u8co/v1/reports/doc_trace", Any },
            // 库存与销售支持报表（ReportsStock*.cs、ReportsCredit.cs、ReportsPrice.cs）：只用 ctx.Conn、登录日期和权限快照。
            new string[] { "/u8co/v1/reports/stock_ledger", Any },
            new string[] { "/u8co/v1/reports/stock_summary", Any },
            new string[] { "/u8co/v1/reports/position_stock", Any },
            new string[] { "/u8co/v1/reports/batch_stock", Any },
            new string[] { "/u8co/v1/reports/customer_credit", Any },
            new string[] { "/u8co/v1/reports/price_list", Any },
            // 固定资产变动单、折旧（ReportsFa*.cs）：只用 ctx.Conn、登录日期和权限快照。
            new string[] { ReportsFaReq.ChangesPath, Any },
            new string[] { ReportsFaReq.DeprPath, Any },
            // 经营管理（总账口径，ReportsMgmt*.cs）：只用 ctx.Conn、账套号、登录日期和权限快照。
            new string[] { ReportsMgmtGlReq.PnlPath, Any },
            new string[] { ReportsMgmtGlReq.MetaPath, Any },
            new string[] { ReportsMgmtGlReq.CashPath, Any },
            // 经营管理销售分析、往来账期（ReportsMgmtSales*.cs、ReportsMgmtArap*.cs）：只用 ctx.Conn、账套号、登录日期和权限快照。
            new string[] { ReportsMgmtSalesReq.Path, Any },
            new string[] { ReportsMgmtArapReq.Path, Any },
            // 期初余额（ReportsOpening*.cs）、凭证附件和单据附件列表（GlAttach、VoucherAttach）：只用 ctx.Conn 和权限快照。
            new string[] { "/u8co/v1/reports/opening_balance", Any },
            // 核销记录（ReportsArapWriteoff*.cs）：只用 ctx.Conn、登录账套和权限快照；能否取消用取消核销的闸门规则只读判断。
            new string[] { "/u8co/v1/reports/arap_writeoffs", Any },
            // 账套体检（ReportsReadiness*.cs）：只用 ctx.Conn、账套号、操作员编码和登录日期。
            new string[] { ReportsReadinessReq.Path, Any },
            new string[] { GlAttach.Path, Any },
            new string[] { VoucherAttach.Path, Any },
            // 名称解析（ArcResolve*.cs）：只用 ctx.Conn、登录日期和权限快照。
            new string[] { ArcResolve.Path, Any },
            // 幂等结果查询（IdemGet）：只读幂等记录，不碰 ctx.Session，也不查库。
            new string[] { IdemGet.Path, Any },
            // 字段说明（MetaFields*.cs）：只用 ctx.Conn、操作员编码和 U8 目录下的 RsXml，不碰 ctx.Session。
            new string[] { MetaFieldsReq.Path, Any },
            // 单据搜索（VoucherSearch*.cs，同 vouchers/list）、档案批量读取（ArcGetMany，同 archives/get）：只用 ctx.Conn 和权限快照。
            // 单据批量读取（LoadMany）不在这里登记：按类型与 vouchers/load 同一名单（Path 见 IsSqlRead）。
            new string[] { VoucherSearch.Path, Any },
            new string[] { ArcGetMany.Path, Any },
            // 单张票据读取（NotesRead）：只用 ctx.Conn 和权限快照，不登录 U8。
            new string[] { NotesReadReq.Path, Any },
            // 应收 / 应付处理记录与期间摘要（ArapProcList*.cs，事件源）：只用 ctx.Conn、登录日期和权限快照。
            new string[] { ArapProcListReq.Path, Any },
            // 权限快照、权限评估（PermSnapshot、PermEvaluate）：只用 ctx.Conn 和权限快照（PermLoad 的只读 SQL）。
            new string[] { PermSnapshot.Path, Any },
            new string[] { PermEvaluate.Path, Any }
        };

        public static bool IsSqlRead(WorkItem item)
        {
            if (item == null || item.Path == null)
            {
                return false;
            }
            string type = item.Type == null ? "" : item.Type.Name ?? "";
            // vouchers/load_many：逐张走 vouchers/load 的读取，线程池按 vouchers/load 的类型名单。
            string path = item.Path == LoadMany.Path ? LoadPath : item.Path;
            return Listed(path, type);
        }

        // 该类型的 vouchers/load 是纯 SQL 读取（load_many 的张数上限用它区分 SQL 与 COM 类型）。
        public static bool IsSqlLoad(VoucherKind kind)
        {
            return kind != null && Listed(LoadPath, kind.Name ?? "");
        }

        static bool Listed(string path, string type)
        {
            for (int i = 0; i < SqlReads.Length; i++)
            {
                string[] row = SqlReads[i];
                if (!string.Equals(row[0], path, StringComparison.Ordinal))
                {
                    continue;
                }
                if (row[1] == Any || string.Equals(row[1], type, StringComparison.Ordinal))
                {
                    return true;
                }
            }
            return false;
        }
    }
}
