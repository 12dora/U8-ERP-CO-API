using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 订单执行 order_execution：销售订单或采购订单逐行列出数量、金额和 U8 自己的累计执行数（订单行上的累计列，
    // 与下游单据逐行合计一致，已在测试账套核对），不另外汇总下游单据。
    // 销售：累计发货 iFHQuantity / iFHMoney、累计出库 foutquantity、累计开票 iKPQuantity / iKPMoney、累计退货 fretquantity、
    // 累计收款 iexchsum（原币）/ imoneysum（本币）。
    // 采购：累计到货 iArrQTY / iArrMoney、累计入库 iReceivedQTY + freceivedqty（直接入库记前者，经到货入库记后者）、
    // 累计开票 iInvQTY / iInvMoney、累计退货 fPoRetQuantity、累计付款 iOriTotal（原币）/ iTotal（本币）。
    // 数据权限与该订单类型的列表相同：功能权限按 voucher:<type>，记录级条件加在表头 h 上。
    internal static class ReportsOrderExec
    {
        const string SaleOpen = "NULLIF(LTRIM(RTRIM(h.cCloser)), N'') IS NULL AND NULLIF(LTRIM(RTRIM(d.cSCloser)), N'') IS NULL"
            + " AND (ABS(ISNULL(d.iFHQuantity,0)) < ABS(ISNULL(d.iQuantity,0))"
            + " OR ABS(ISNULL(d.foutquantity,0)) < ABS(ISNULL(d.iQuantity,0))"
            + " OR ABS(ISNULL(d.iKPQuantity,0)) < ABS(ISNULL(d.iQuantity,0)))";
        const string PoOpen = "ISNULL(h.cState,0) <> 2 AND NULLIF(LTRIM(RTRIM(h.cCloser)), N'') IS NULL"
            + " AND NULLIF(LTRIM(RTRIM(d.cbCloser)), N'') IS NULL"
            + " AND (ABS(ISNULL(d.iReceivedQTY,0) + ISNULL(d.freceivedqty,0)) < ABS(ISNULL(d.iQuantity,0))"
            + " OR ABS(ISNULL(d.iInvQTY,0)) < ABS(ISNULL(d.iQuantity,0)))";
        const string Common = "SELECT TOP (?) h.{HID} AS head_id, h.{CODE} AS code, CONVERT(varchar(10), h.{DATE}, 23) AS doc_date,"
            + " h.{PARTNER} AS partner, pt.{PNAME} AS partner_name, h.cexch_name AS currency,"
            + " CASE WHEN NULLIF(LTRIM(RTRIM(h.cVerifier)), N'') IS NULL THEN 0 ELSE 1 END AS verified,"
            + " d.{LID} AS line_id, d.{ROWNO} AS row_no, d.cInvCode AS inv_code, i.cInvName AS inv_name, i.cInvStd AS inv_std,"
            + " CONVERT(varchar(10), d.{DUE}, 23) AS due_date, CASE WHEN {OPEN} THEN 1 ELSE 0 END AS is_open,"
            + " d.iQuantity AS qty, d.iSum AS amount, d.iNatSum AS nat_amount,";
        const string SaleSql = Common + " CASE WHEN NULLIF(LTRIM(RTRIM(h.cCloser)), N'') IS NULL"
            + " AND NULLIF(LTRIM(RTRIM(d.cSCloser)), N'') IS NULL THEN 0 ELSE 1 END AS closed,"
            + " d.iFHQuantity AS shipped_qty, d.iFHMoney AS shipped_amount, d.foutquantity AS out_qty,"
            + " d.iKPQuantity AS invoiced_qty, d.iKPMoney AS invoiced_amount, d.fretquantity AS returned_qty,"
            + " d.iexchsum AS received_amount, d.imoneysum AS received_nat_amount"
            + " FROM SO_SOMain h JOIN SO_SODetails d ON d.ID=h.ID LEFT JOIN Customer pt ON pt.cCusCode=h.cCusCode"
            + " LEFT JOIN Inventory i ON i.cInvCode=d.cInvCode WHERE 1=1";
        const string PoSql = Common + " CASE WHEN ISNULL(h.cState,0) <> 2 AND NULLIF(LTRIM(RTRIM(h.cCloser)), N'') IS NULL"
            + " AND NULLIF(LTRIM(RTRIM(d.cbCloser)), N'') IS NULL THEN 0 ELSE 1 END AS closed,"
            + " d.iArrQTY AS arrived_qty, d.iArrMoney AS arrived_amount,"
            + " ISNULL(d.iReceivedQTY,0) + ISNULL(d.freceivedqty,0) AS in_qty,"
            + " d.iInvQTY AS invoiced_qty, d.iInvMoney AS invoiced_amount, d.fPoRetQuantity AS returned_qty,"
            + " d.iOriTotal AS paid_amount, d.iTotal AS paid_nat_amount"
            + " FROM PO_Pomain h JOIN PO_Podetails d ON d.POID=h.POID LEFT JOIN Vendor pt ON pt.cVenCode=h.cVenCode"
            + " LEFT JOIN Inventory i ON i.cInvCode=d.cInvCode WHERE 1=1";

        static readonly string[] SaleQty = new string[] { "shipped_qty", "out_qty", "invoiced_qty", "returned_qty" };
        static readonly string[] SaleMoney = new string[]
        {
            "shipped_amount", "invoiced_amount", "received_amount", "received_nat_amount"
        };
        static readonly string[] PoQty = new string[] { "arrived_qty", "in_qty", "invoiced_qty", "returned_qty" };
        static readonly string[] PoMoney = new string[] { "arrived_amount", "invoiced_amount", "paid_amount", "paid_nat_amount" };

        // 每种订单：{ 表头 id, 单号, 日期, 往来单位, 往来单位名称列, 行 id, 行号, 计划日期 }。
        static string[] Cols(bool sale)
        {
            if (sale)
            {
                return new string[] { "ID", "cSOCode", "dDate", "cCusCode", "cCusName", "iSOsID", "iRowNo", "dPreDate" };
            }
            return new string[] { "POID", "cPOID", "dPODate", "cVenCode", "cVenName", "ID", "ivouchrowno", "dArriveDate" };
        }

        public static ApiResult Run(WorkContext ctx, ReportArgs ignored)
        {
            ExecArgs a = ReportsTraceReq.ParseExec(ctx.Item.Body);
            bool sale = a.Type == "sale_order";
            PermContext p = PermCheck.Of(ctx);
            PermRule rule = PermRegistry.ForKey("voucher:" + a.Type);
            PermCheck.RequireRule(p, rule);
            string[] c = Cols(sale);
            string open = sale ? SaleOpen : PoOpen;
            StringBuilder text = new StringBuilder((sale ? SaleSql : PoSql).Replace("{HID}", c[0]).Replace("{CODE}", c[1])
                .Replace("{DATE}", c[2]).Replace("{PARTNER}", c[3]).Replace("{PNAME}", c[4]).Replace("{LID}", c[5])
                .Replace("{ROWNO}", c[6]).Replace("{DUE}", c[7]).Replace("{OPEN}", open));
            List<object> args = new List<object>();
            args.Add(a.Limit + 1);
            Filter(text, args, a, c);
            if (a.OnlyOpen)
            {
                text.Append(" AND ").Append(open);
            }
            PermSql.AppendRule(text, args, p, rule, "h");
            After(text, args, a.After, c);
            text.Append(" ORDER BY h.").Append(c[0]).Append(", d.").Append(c[5]);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, text.ToString(), args.ToArray(), a.Limit + 1);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < a.Limit; i++)
            {
                items.Add(Item(rows[i], sale));
            }
            Dictionary<string, object> body = Reports.Body();
            body["type"] = a.Type;
            body["only_open"] = a.OnlyOpen;
            body["items"] = items;
            body["next"] = rows.Count > a.Limit ? Next(rows[a.Limit - 1]) : null;
            return ApiResult.Ok(body);
        }

        // 过滤条件只拼占位符，取值只进参数。日期按表头单据日期，含两端。
        static void Filter(StringBuilder text, List<object> args, ExecArgs a, string[] c)
        {
            if (a.Ids != null)
            {
                text.Append(" AND h.").Append(c[0]).Append(" IN (");
                for (int i = 0; i < a.Ids.Length; i++)
                {
                    text.Append(i == 0 ? "?" : ", ?");
                    args.Add(a.Ids[i]);
                }
                text.Append(")");
            }
            Eq(text, args, "h." + c[1], a.Code);
            Eq(text, args, "h." + c[3], a.Partner);
            if (a.DateFrom.Length > 0)
            {
                text.Append(" AND h.").Append(c[2]).Append(" >= CONVERT(date, ?, 23)");
                args.Add(a.DateFrom);
            }
            if (a.DateTo.Length > 0)
            {
                text.Append(" AND h.").Append(c[2]).Append(" < DATEADD(day, 1, CONVERT(date, ?, 23))");
                args.Add(a.DateTo);
            }
        }

        static void Eq(StringBuilder text, List<object> args, string col, string value)
        {
            if (value.Length > 0)
            {
                text.Append(" AND ").Append(col).Append("=?");
                args.Add(value);
            }
        }

        // after 游标：表头 id + 行 id（都是正整数）。
        static void After(StringBuilder text, List<object> args, string after, string[] c)
        {
            string[] parts = Reports.Uncursor(after, 2);
            if (parts == null)
            {
                return;
            }
            int head;
            int line;
            if (!int.TryParse(parts[0], NumberStyles.None, CultureInfo.InvariantCulture, out head)
                || !int.TryParse(parts[1], NumberStyles.None, CultureInfo.InvariantCulture, out line))
            {
                throw GlReq.Bad("after 游标无效");
            }
            text.Append(" AND (h.").Append(c[0]).Append(">? OR (h.").Append(c[0]).Append("=? AND d.").Append(c[5])
                .Append(">?))");
            args.Add(head);
            args.Add(head);
            args.Add(line);
        }

        static string Next(Dictionary<string, object> row)
        {
            return Reports.Cursor(GlSql.Col(row, "head_id"), GlSql.Col(row, "line_id"));
        }

        static Dictionary<string, object> Item(Dictionary<string, object> row, bool sale)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["id"] = GlSql.Int(row, "head_id");
            item["code"] = Reports.Text(row, "code");
            item["date"] = Reports.Text(row, "doc_date");
            item["partner"] = Reports.Text(row, "partner");
            item["partner_name"] = Reports.Text(row, "partner_name");
            item["currency"] = Reports.Text(row, "currency");
            item["verified"] = GlSql.Bit(row, "verified");
            item["closed"] = GlSql.Bit(row, "closed");
            item["open"] = GlSql.Bit(row, "is_open");
            item["line_id"] = GlSql.Int(row, "line_id");
            item["row_no"] = GlSql.Int(row, "row_no");
            item["inv_code"] = Reports.Text(row, "inv_code");
            item["inv_name"] = Reports.Text(row, "inv_name");
            item["inv_std"] = Reports.Text(row, "inv_std");
            item["due_date"] = Reports.Text(row, "due_date");
            item["qty"] = Reports.Qty(row, "qty");
            item["amount"] = GlSql.Money(row, "amount");
            item["nat_amount"] = GlSql.Money(row, "nat_amount");
            string[] qty = sale ? SaleQty : PoQty;
            string[] money = sale ? SaleMoney : PoMoney;
            for (int i = 0; i < qty.Length; i++)
            {
                item[qty[i]] = Reports.Qty(row, qty[i]);
            }
            for (int i = 0; i < money.Length; i++)
            {
                item[money[i]] = GlSql.Money(row, money[i]);
            }
            return item;
        }
    }
}
