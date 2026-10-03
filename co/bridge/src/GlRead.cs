using System;
using System.Collections.Generic;

namespace U8Co
{
    // 读凭证：纯 SQL，只用 ctx.Conn（读线程没有 U8 登录）。年度用登录年度，表用 GL_accvouch，不用 gl_v_accvouch。
    internal static class GlRead
    {
        const int MaxLines = 10000;

        const string LineSql = "SELECT v.inid, v.isignseq, v.ccode, c.ccode_name, v.cdigest, v.md, v.mc, v.md_f, v.mc_f,"
            + " v.nd_s, v.nc_s, v.cexch_name, v.nfrat, v.cdept_id, v.cperson_id, v.ccus_id, v.csup_id, v.citem_class,"
            + " v.citem_id, v.csettle, v.cn_id, CONVERT(varchar(10), v.dt_date, 23) dt_date,"
            + " CONVERT(varchar(10), v.dbill_date, 23) dbill_date, v.idoc, v.cbill, v.ccheck,"
            + " CONVERT(varchar(10), v.daudit_date, 23) daudit_date, v.ccashier, v.cbook, v.ibook, v.iflag,"
            + " v.coutsysname, v.coutsign, v.coutid, v.coutno_id, v.cblueoutno_id"
            + " FROM GL_accvouch v LEFT JOIN code c ON c.iyear=v.iyear AND c.ccode=v.ccode"
            + " WHERE v.iyear=? AND v.iperiod=? AND v.csign=? AND v.ino_id=? ORDER BY v.inid";

        const string CashSql = "SELECT inid, cCashItem, md, mc FROM GL_CashTable"
            + " WHERE iyear=? AND iPeriod=? AND iSignSeq=? AND iNo_id=? ORDER BY inid, i_id";

        public static ApiResult Load(WorkContext ctx, GlKey key)
        {
            object conn = ctx.Conn;
            List<Dictionary<string, object>> rows = Rows.Query(conn, LineSql, GlSql.KeyArgs(key), MaxLines);
            if (rows.Count == 0)
            {
                throw new BridgeException(404, "not_found", "凭证不存在：" + key.Text());
            }
            // 数据权限：一条科目都不放行时 403。
            PermHook.Lines(ctx, rows);
            int seq = GlSql.Int(rows[0], "isignseq");
            List<Dictionary<string, object>> cash = Rows.Query(conn, CashSql,
                new object[] { key.Year, key.Period, seq, key.No }, MaxLines);
            List<object> lines = new List<object>();
            foreach (Dictionary<string, object> row in rows)
            {
                lines.Add(Line(row, cash));
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["voucher"] = Head(key, rows[0]);
            body["lines"] = lines;
            return ApiResult.Ok(body);
        }

        static Dictionary<string, object> Head(GlKey key, Dictionary<string, object> row)
        {
            int flag = GlSql.Int(row, "iflag");
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["period"] = key.Period;
            head["sign"] = key.Sign;
            head["no"] = key.No;
            head["date"] = GlSql.Col(row, "dbill_date");
            head["attachments"] = GlSql.Int(row, "idoc");
            head["maker"] = GlSql.Col(row, "cbill");
            head["checker"] = GlSql.Col(row, "ccheck");
            head["audit_date"] = GlSql.Col(row, "daudit_date");
            head["cashier"] = GlSql.Col(row, "ccashier");
            head["poster"] = GlSql.Col(row, "cbook");
            head["posted"] = GlSql.Int(row, "ibook") != 0;
            head["void"] = flag == 1;
            head["error"] = flag == 2;
            head["source_system"] = GlSql.Col(row, "coutsysname");
            head["source_sign"] = GlSql.Col(row, "coutsign");
            head["source_no"] = GlSql.Col(row, "coutid");
            head["out_no"] = GlSql.Col(row, "coutno_id");
            // 红字冲销凭证：被冲销的原凭证的外部业务号（cblueoutno_id）；不是红字冲销凭证时为空串。
            head["blue_out_no"] = GlSql.Col(row, "cblueoutno_id");
            return head;
        }

        static Dictionary<string, object> Line(Dictionary<string, object> row, List<Dictionary<string, object>> cash)
        {
            Dictionary<string, object> line = new Dictionary<string, object>();
            string entry = GlSql.Col(row, "inid");
            line["entry"] = GlSql.Int(row, "inid");
            line["account"] = GlSql.Col(row, "ccode");
            line["account_name"] = GlSql.Col(row, "ccode_name");
            line["digest"] = GlSql.Col(row, "cdigest");
            line["debit"] = GlSql.Money(row, "md");
            line["credit"] = GlSql.Money(row, "mc");
            line["debit_fc"] = GlSql.Money(row, "md_f");
            line["credit_fc"] = GlSql.Money(row, "mc_f");
            line["qty_debit"] = GlSql.Num(row, "nd_s") ?? 0.0;
            line["qty_credit"] = GlSql.Num(row, "nc_s") ?? 0.0;
            line["currency"] = GlSql.Col(row, "cexch_name");
            line["rate"] = GlSql.Num(row, "nfrat");
            line["dept"] = GlSql.Col(row, "cdept_id");
            line["person"] = GlSql.Col(row, "cperson_id");
            line["customer"] = GlSql.Col(row, "ccus_id");
            line["supplier"] = GlSql.Col(row, "csup_id");
            line["item_class"] = GlSql.Col(row, "citem_class");
            line["item"] = GlSql.Col(row, "citem_id");
            line["settle"] = GlSql.Col(row, "csettle");
            line["doc_no"] = GlSql.Col(row, "cn_id");
            line["doc_date"] = GlSql.Col(row, "dt_date");
            List<object> flows = new List<object>();
            foreach (Dictionary<string, object> c in cash)
            {
                if (GlSql.Col(c, "inid") != entry)
                {
                    continue;
                }
                Dictionary<string, object> flow = new Dictionary<string, object>();
                flow["item"] = GlSql.Col(c, "cCashItem");
                flow["debit"] = GlSql.Money(c, "md");
                flow["credit"] = GlSql.Money(c, "mc");
                flows.Add(flow);
            }
            line["cash_flow"] = flows;
            return line;
        }
    }
}
