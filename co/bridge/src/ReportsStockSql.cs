using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 库存报表共用的 SQL 片段。出入库流水取七类收发记录（表头 + 表体）：采购入库 01、其他入库 08、其他出库 09、
    // 产成品入库 10、材料出库 11、销售出库 32、库存期初 34。调拨、盘点、形态转换在 U8 里生成 08 / 09，不另算。
    // 已审核（表头 cHandler 非空）的这七类合计与 CurrentStock.iQuantity 逐（仓库、存货）一致（已在测试账套核对）。
    // 表体 AutoID 在这七张表之间不重复（U8 共用一个 rd 号段，已在测试账套核对），翻页按（日期、AutoID）。
    // 表名只来自下面的固定表，调用方的值只进参数。
    internal static class ReportsStockSql
    {
        // { 单据类型代码, 表头, 表体, 对外的单据类型名 }。
        static readonly string[][] Tables = new string[][]
        {
            new string[] { "01", "RdRecord01", "rdrecords01", "purchase_in" },
            new string[] { "08", "RdRecord08", "rdrecords08", "other_in" },
            new string[] { "09", "RdRecord09", "rdrecords09", "other_out" },
            new string[] { "10", "rdrecord10", "rdrecords10", "product_in" },
            new string[] { "11", "rdrecord11", "rdrecords11", "material_out" },
            new string[] { "32", "rdrecord32", "rdrecords32", "sale_out" },
            new string[] { "34", "rdrecord34", "rdrecords34", "stock_opening" }
        };

        // 派生表 m：vt、id、autoid、code、ddate、rd（1 入 0 出）、cWhCode、cInvCode、batch、qty（单据上的原数，红字为负）、
        // handler（审核人）、rdcode（收发类别）、src（来源）。仓库、存货列名与 PermRegistry 的 stock 规则一致。
        internal static readonly string Moves = BuildMoves();

        // 入库为正、出库为负的数量。
        internal const string Signed = "CASE WHEN m.rd=1 THEN m.qty ELSE -m.qty END";

        static string BuildMoves()
        {
            StringBuilder sb = new StringBuilder("(");
            for (int i = 0; i < Tables.Length; i++)
            {
                string[] t = Tables[i];
                sb.Append(i == 0 ? "" : " UNION ALL ")
                    .Append("SELECT N'").Append(t[0]).Append("' vt, h.ID id, b.AutoID autoid, h.cCode code, h.dDate ddate,")
                    .Append(" h.bRdFlag rd, h.cWhCode, b.cInvCode, b.cBatch batch, b.iQuantity qty, h.cHandler handler,")
                    .Append(" h.cRdCode rdcode, h.cSource src FROM ").Append(t[1]).Append(" h JOIN ").Append(t[2])
                    .Append(" b ON b.ID=h.ID");
            }
            return sb.Append(") m").ToString();
        }

        internal static string TypeOf(string vt)
        {
            for (int i = 0; i < Tables.Length; i++)
            {
                if (Tables[i][0] == vt)
                {
                    return Tables[i][3];
                }
            }
            return null;
        }

        // 仓库、批次、审核状态，最后是数据权限（仓库、存货）。权限条件必须放在 WHERE 最后。
        internal static void MoveFilter(StringBuilder sb, List<object> ps, WorkContext ctx, StockReportArgs s)
        {
            Equal(sb, ps, "m.cWhCode", s.Wh);
            Equal(sb, ps, "m.batch", s.Batch);
            if (!s.Unverified)
            {
                sb.Append(" AND ISNULL(m.handler, N'')<>N''");
            }
            PermHook.Where(sb, ps, ctx, "m");
        }

        internal static void Equal(StringBuilder sb, List<object> ps, string column, string value)
        {
            if (value == null || value.Length == 0)
            {
                return;
            }
            sb.Append(" AND ").Append(column).Append("=?");
            ps.Add(value);
        }

        // 缺省日期取登录日期（date）。
        internal static string DateOr(WorkContext ctx, string value)
        {
            if (value != null && value.Length > 0)
            {
                return value;
            }
            return (ctx.Item.Date ?? "").Trim();
        }

        // 按主键翻页的游标：只有一段整数。
        internal static int AfterId(string after)
        {
            string[] parts = Reports.Uncursor(after, 1);
            if (parts == null)
            {
                return 0;
            }
            int id;
            if (!int.TryParse(parts[0], NumberStyles.Integer, CultureInfo.InvariantCulture, out id))
            {
                throw GlReq.Bad("after 游标无效");
            }
            return id;
        }

        // 可空的数量（六位小数）；列为空时为 null。
        internal static object OptQty(Dictionary<string, object> row, string name)
        {
            if (GlSql.Col(row, name).Length == 0)
            {
                return null;
            }
            return Reports.Qty(row, name);
        }

        internal static Dictionary<string, object> Page(List<object> items, string next)
        {
            Dictionary<string, object> body = Reports.Body();
            body["items"] = items;
            body["next"] = next;
            return body;
        }
    }
}
