using System;
using System.Collections.Generic;

namespace U8Co
{
    // 总账凭证附件列表 gl/vouchers/attachments/list（只读，全是 SQL，走读线程，只用 ctx.Conn）。
    // 凭证上的 attachments（GL_accvouch.idoc）只是「附单据数」；电子附件在 GL_AccAttachs（PZAssFile.ocx 写入），
    // 按（年度、期间、凭证类别序号 isignseq、凭证号）挂在凭证上，文件本身在 U8 文件服务器，这里只列清单，不给下载。
    // 权限同凭证读取（PermRegistry "gl"）。GL_AccAttachs 的插入触发器会补 iyear；iyear 为空的旧行按 iYPeriod
    // （年 × 100 + 期间，未经实测）认年度，两者都为空才一并算。
    internal static class GlAttach
    {
        internal const string Op = "attachments/list";
        internal const string Path = Requests.GlRoot + Op;
        const int MaxFiles = 500;

        const string HeadSql = "SELECT v.ccode, v.isignseq, v.idoc FROM GL_accvouch v"
            + " WHERE v.iyear=? AND v.iperiod=? AND v.csign=? AND v.ino_id=? ORDER BY v.inid";
        const string ListSql = "SELECT TOP (?) Inid, CClientFileName, CServerFileName,"
            + " CONVERT(varchar(19), SubmitTime, 120) submitted, csource FROM GL_AccAttachs"
            + " WHERE (iyear=? OR (ISNULL(iyear,0)=0 AND ISNULL(iYPeriod,0) IN (0, ?)))"
            + " AND Iperiod=? AND isignseq=? AND ino_id=? ORDER BY Inid";

        public static bool Owns(string op)
        {
            return op == Op;
        }

        public static ApiResult Handle(WorkContext ctx)
        {
            GlKey key = GlReq.Key(ctx.Item.Body, GlState.LoginYear(ctx));
            List<Dictionary<string, object>> lines = Rows.Query(ctx.Conn, HeadSql, GlSql.KeyArgs(key), 10000);
            if (lines.Count == 0)
            {
                throw new BridgeException(404, "not_found", "凭证不存在：" + key.Text());
            }
            // 数据权限：同凭证读取。
            PermHook.Lines(ctx, lines);
            int seq = GlSql.Int(lines[0], "isignseq");
            object[] args = new object[] { MaxFiles + 1, key.Year, key.Year * 100 + key.Period, key.Period, seq, key.No };
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, ListSql, args, MaxFiles + 1);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < MaxFiles; i++)
            {
                items.Add(Item(rows[i]));
            }
            Dictionary<string, object> voucher = new Dictionary<string, object>();
            voucher["period"] = key.Period;
            voucher["sign"] = key.Sign;
            voucher["no"] = key.No;
            voucher["attachments"] = GlSql.Int(lines[0], "idoc");
            Dictionary<string, object> body = Reports.Body();
            body["voucher"] = voucher;
            body["items"] = items;
            body["truncated"] = rows.Count > MaxFiles;
            return ApiResult.Ok(body);
        }

        static Dictionary<string, object> Item(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["id"] = GlSql.Int(row, "Inid");
            item["name"] = Reports.Text(row, "CClientFileName");
            item["file_id"] = Reports.Text(row, "CServerFileName");
            item["submitted_at"] = Reports.Text(row, "submitted");
            item["source"] = Reports.Text(row, "csource");
            return item;
        }
    }
}
