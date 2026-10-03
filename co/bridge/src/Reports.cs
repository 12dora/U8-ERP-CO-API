using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 只读报表 reports/<name>。全部是 SQL，跑在读线程池上，只用 ctx.Conn，不碰 ctx.Session。
    internal static class Reports
    {
        internal static readonly string[] Names = new string[]
        {
            "close_status", "gl_balance", "gl_aux_balance", "arap_balance", "arap_aging", "bom",
            "arap_detail", "gl_detail",
            ReportsTraceReq.ExecName, ReportsTraceReq.TraceName,
            "stock_ledger", "stock_summary", "position_stock", "batch_stock", "customer_credit", "price_list",
            // 期初余额（ReportsOpening*.cs）。
            ReportsOpeningReq.Name,
            // 核销记录（ReportsArapWriteoff*.cs）。
            ReportsArapWriteoffReq.Name,
            // 账套体检（ReportsReadiness*.cs）。
            ReportsReadinessReq.Name,
            // 固定资产变动单、折旧（ReportsFa*.cs）。
            ReportsFaReq.ChangesName, ReportsFaReq.DeprName,
            // 经营管理（总账口径：损益、数据水位、资金存货，ReportsMgmtPnl*.cs、ReportsMgmtMeta.cs、ReportsMgmtCash*.cs）。
            ReportsMgmtGlReq.PnlName, ReportsMgmtGlReq.MetaName, ReportsMgmtGlReq.CashName,
            // 经营管理（销售分析、往来账期，ReportsMgmtSales*.cs、ReportsMgmtArap*.cs）。
            ReportsMgmtSalesReq.Name, ReportsMgmtArapReq.Name
        };
        static readonly Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>> Runners = BuildRunners();

        // 登录前校验，只抛 400。
        public static void Check(string name, Dictionary<string, object> body)
        {
            ReportsReq.Parse(name, body);
        }

        // 登录子系统：总账报表 GL，往来按 side 用 AR / AP，月结状态与物料清单用 SA。
        public static string SubOf(string name, Dictionary<string, object> body)
        {
            if (name == "gl_balance" || name == "gl_aux_balance" || name == "gl_detail")
            {
                return "GL";
            }
            if (name == "arap_balance" || name == "arap_aging" || name == "arap_detail")
            {
                string side = Requests.Field(body, "side") as string;
                return side == "ap" ? "AP" : "AR";
            }
            return OwnedSub(name, body) ?? "SA";
        }

        // 参数另有专门类的报表；都不是时返回 null。
        static string OwnedSub(string name, Dictionary<string, object> body)
        {
            // 订单执行、单据追溯按请求里单据类型的登录子系统（同 vouchers/load）。
            if (ReportsTraceReq.Owns(name))
            {
                return ReportsTraceReq.SubOf(body);
            }
            // 库存类报表 ST，客户信用和销售价格 SA，供应商价格 PU；期初余额：库存 ST，应收应付按 side，总账 GL。
            // 两者对不是自己的报表都返回 null。
            // 核销记录按 flag 用 AR / AP；账套体检 SA（同 close_status）。
            return ReportsStockReq.SubOf(name, body) ?? ReportsOpeningReq.SubOf(name, body)
                ?? ReportsArapWriteoffReq.SubOf(name, body) ?? ReportsReadinessReq.SubOf(name)
                ?? ReportsMgmtGlReq.SubOf(name) ?? MgmtSalesArapSub(name, body);
        }

        // 经营管理销售分析 SA，往来账期按 side 用 AR / AP（同 arap_aging）；都不是时返回 null。
        static string MgmtSalesArapSub(string name, Dictionary<string, object> body)
        {
            return ReportsMgmtSalesReq.SubOf(name) ?? ReportsMgmtArapReq.SubOf(name, body);
        }

        public static ApiResult Handle(WorkContext ctx, string name)
        {
            ReportArgs a = ReportsReq.Parse(name, ctx.Item.Body);
            if (a.FiscalYear == 0)
            {
                a.FiscalYear = GlState.LoginYear(ctx);
            }
            if (a.AsOf != null && a.AsOf.Length == 0)
            {
                a.AsOf = (ctx.Item.Date ?? "").Trim();
            }
            Func<WorkContext, ReportArgs, ApiResult> run;
            if (!Runners.TryGetValue(a.Name, out run))
            {
                throw GlReq.Bad("未知的报表 " + a.Name);
            }
            return run(ctx, a);
        }

        static Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>> BuildRunners()
        {
            Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>> map =
                new Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>>(StringComparer.Ordinal);
            map.Add("close_status", ReportsGl.CloseStatus);
            map.Add("gl_balance", ReportsGl.Balance);
            map.Add("gl_aux_balance", ReportsAux.Balance);
            map.Add("arap_balance", ReportsArap.Balance);
            map.Add("arap_aging", ReportsArapAging.Aging);
            map.Add("bom", ReportsBom.Expand);
            map.Add("arap_detail", ReportsArapDetail.Detail);
            map.Add("gl_detail", ReportsGlDetail.Detail);
            map.Add(ReportsTraceReq.ExecName, ReportsOrderExec.Run);
            map.Add(ReportsTraceReq.TraceName, ReportsTrace.Run);
            map.Add("stock_ledger", ReportsStockLedger.Ledger);
            map.Add("stock_summary", ReportsStockSummary.Summary);
            map.Add("position_stock", ReportsStockPos.Positions);
            map.Add("batch_stock", ReportsStockPos.Batches);
            map.Add("customer_credit", ReportsCredit.Credit);
            map.Add("price_list", ReportsPrice.Prices);
            map.Add(ReportsOpeningReq.Name, ReportsOpening.Run);
            map.Add(ReportsArapWriteoffReq.Name, ReportsArapWriteoff.Run);
            map.Add(ReportsReadinessReq.Name, ReportsReadiness.Run);
            map.Add(ReportsFaReq.ChangesName, ReportsFa.Changes);
            map.Add(ReportsFaReq.DeprName, ReportsFa.Depreciation);
            map.Add(ReportsMgmtGlReq.PnlName, ReportsMgmtPnl.Run);
            map.Add(ReportsMgmtGlReq.MetaName, ReportsMgmtMeta.Run);
            map.Add(ReportsMgmtGlReq.CashName, ReportsMgmtCash.Run);
            map.Add(ReportsMgmtSalesReq.Name, ReportsMgmtSales.Run);
            map.Add(ReportsMgmtArapReq.Name, ReportsMgmtArap.Run);
            return map;
        }

        public static Dictionary<string, object> Body()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            return body;
        }

        // 余额方向：正数借、负数贷，四舍五入到分后为 0 记平。
        public static void PutBalance(Dictionary<string, object> item, string prefix, decimal signed)
        {
            decimal value = decimal.Round(signed, 2, MidpointRounding.AwayFromZero);
            item[prefix + "_dir"] = value > 0 ? "借" : (value < 0 ? "贷" : "平");
            item[prefix + "_debit"] = value > 0 ? value : 0m;
            item[prefix + "_credit"] = value < 0 ? -value : 0m;
        }

        // SQL 片段 " AND (col LIKE ? OR col LIKE ?)"：只按个数拼占位符，前缀本身只进参数（已限定无通配符的字符）。
        public static void LikeAny(StringBuilder sql, List<object> args, string col, string[] prefixes, bool negate)
        {
            if (prefixes == null || prefixes.Length == 0)
            {
                return;
            }
            sql.Append(negate ? " AND NOT (" : " AND (");
            for (int i = 0; i < prefixes.Length; i++)
            {
                sql.Append(i == 0 ? "" : " OR ").Append(col).Append(" LIKE ?");
                args.Add(prefixes[i] + "%");
            }
            sql.Append(")");
        }

        // after / next 游标：各段用 0x1F 连起来再做 base64url，调用方原样传回，不要解析。
        public static string Cursor(params string[] parts)
        {
            byte[] raw = Encoding.UTF8.GetBytes(string.Join("\u001f", parts));
            return Convert.ToBase64String(raw).TrimEnd('=').Replace('+', '-').Replace('/', '_');
        }

        public static string[] Uncursor(string text, int count)
        {
            if (text == null || text.Length == 0)
            {
                return null;
            }
            string[] parts;
            try
            {
                string b64 = text.Replace('-', '+').Replace('_', '/');
                b64 = b64.PadRight(b64.Length + (4 - b64.Length % 4) % 4, '=');
                parts = Encoding.UTF8.GetString(Convert.FromBase64String(b64)).Split('\u001f');
            }
            catch (FormatException)
            {
                throw GlReq.Bad("after 游标无效");
            }
            if (parts.Length != count)
            {
                throw GlReq.Bad("after 游标无效");
            }
            return parts;
        }

        public static string Text(Dictionary<string, object> row, string name)
        {
            string text = GlSql.Col(row, name);
            return text.Length == 0 ? null : text;
        }

        public static decimal Qty(Dictionary<string, object> row, string name)
        {
            decimal value;
            decimal.TryParse(GlSql.Col(row, name), NumberStyles.Float, CultureInfo.InvariantCulture, out value);
            return decimal.Round(value, 6, MidpointRounding.AwayFromZero);
        }
    }
}
