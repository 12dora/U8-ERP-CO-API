using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // reports/mgmt/meta：经营管理查询的数据水位与期间状态，供 API 层定缓存键和有效期（每次请求都会先查它，只跑三条小查询）。
    // modules：各模块的启用日期（AccInformation，空表示未启用）。periods：1–12 期各模块结账标志（GL_mend，总账列是 bflag）、
    // 未记账凭证张数、是否已做期间损益结转。watermarks：本年度凭证一次聚合（行数、已记账行数、作废行数、最大制单日期、
    // 科目与期间的校验和 CHECKSUM_AGG(CHECKSUM(ccode, iperiod))、分录内容校验和 gl_content_checksum（主键、科目、期间、
    // 借贷金额、记账与作废标志逐行 BINARY_CHECKSUM 再聚合）；新增、删除、记账、作废、改金额、改科目都会变），整表最大
    // i_id（主键索引，任何年度新增都变），科目档案、结账表的最大 rowversion（pubufts），各单据表的最大 rowversion，存货明细账、
    // 往来明细的行数与最大主键。没有某模块数据时对应项为 null。GL_accvouch 没有 rowversion 列；校验和只取科目、期间（有同时
    // 覆盖这些列的索引时只读索引），只改辅助项、摘要且金额、科目不变的修改不改变水位。
    // 水位不再给金额（去掉 gl_debit / gl_credit 借贷方合计）。本路由只按「结账状态」类功能权限放行、不按科目过滤，
    // 金额合计会让没有凭证查询或余额表权限的人看到全年发生额；改用不可还原金额的内容校验和 gl_content_checksum。
    // 校验和是 32 位整数，偶有碰撞时漏一次失效，由 gl_lines、gl_max_id 等其他水位兜底。
    internal static class ReportsMgmtMeta
    {
        static readonly string[] Modules = new string[] { "GL", "SA", "PU", "ST", "IA", "AR", "AP" };
        // 模块 → AccInformation 的启用日期项（cSysID、cName 都是常量）。
        static readonly string[][] StartKeys = new string[][]
        {
            new string[] { "GL", "dGLStartDate" }, new string[] { "SA", "dSaleStartDate" }, new string[] { "PU", "dPUStartDate" },
            new string[] { "ST", "dSTStartDate" }, new string[] { "IA", "dIAStartDate" }, new string[] { "AR", "dARStartDate" },
            new string[] { "AP", "dAPStartDate" }, new string[] { "FA", "dStartDate" }
        };
        const string CloseSql = "SELECT iperiod, CONVERT(int, ISNULL(bflag, 0)) GL, CONVERT(int, ISNULL(bflag_SA, 0)) SA,"
            + " CONVERT(int, ISNULL(bflag_PU, 0)) PU, CONVERT(int, ISNULL(bflag_ST, 0)) ST, CONVERT(int, ISNULL(bflag_IA, 0)) IA,"
            + " CONVERT(int, ISNULL(bflag_AR, 0)) AR, CONVERT(int, ISNULL(bflag_AP, 0)) AP FROM GL_mend"
            + " WHERE iyear = ? AND iperiod BETWEEN 1 AND 12 ORDER BY iperiod";
        // ? 的顺序：年度。本年度凭证只聚合一遍（有覆盖 iyear、ibook、iflag、md、mc、dbill_date、ccode、iperiod 的索引时只读索引）。
        internal const string MarkSql = "SELECT g.gl_lines, g.gl_posted, g.gl_void, g.gl_max_date,"
            + " g.gl_checksum, g.gl_content_checksum,"
            + " (SELECT MAX(i_id) FROM GL_accvouch) AS gl_max_id,"
            + " (SELECT CONVERT(varchar(20), CONVERT(bigint, MAX(pubufts))) FROM code) AS code_ufts,"
            + " (SELECT CONVERT(varchar(20), CONVERT(bigint, MAX(pubufts))) FROM GL_mend) AS close_ufts,"
            + " (SELECT CONVERT(varchar(20), CONVERT(bigint, MAX(ufts))) FROM SaleBillVouch) AS sa_invoice_ufts,"
            + " (SELECT CONVERT(varchar(20), CONVERT(bigint, MAX(ufts))) FROM PurBillVouch) AS pu_invoice_ufts,"
            + " (SELECT CONVERT(varchar(20), CONVERT(bigint, MAX(ufts))) FROM RdRecord01) AS purchase_in_ufts,"
            + " (SELECT CONVERT(varchar(20), CONVERT(bigint, MAX(ufts))) FROM rdrecord10) AS product_in_ufts,"
            + " (SELECT CONVERT(varchar(20), CONVERT(bigint, MAX(Ufts))) FROM Ap_Note) AS note_ufts,"
            + " (SELECT COUNT(*) FROM IA_Subsidiary) AS ia_count, (SELECT MAX(AutoID) FROM IA_Subsidiary) AS ia_max_id,"
            + " (SELECT CONVERT(varchar(10), MAX(dVouDate), 23) FROM IA_Subsidiary) AS ia_max_date,"
            + " (SELECT COUNT(*) FROM Ar_Detail) AS ar_count, (SELECT MAX(Auto_ID) FROM Ar_Detail) AS ar_max_id,"
            + " (SELECT COUNT(*) FROM Ap_Detail) AS ap_count, (SELECT MAX(Auto_ID) FROM Ap_Detail) AS ap_max_id"
            + " FROM (SELECT COUNT(*) AS gl_lines, SUM(CASE WHEN ibook = 1 THEN 1 ELSE 0 END) AS gl_posted,"
            + " SUM(CASE WHEN iflag = 1 THEN 1 ELSE 0 END) AS gl_void,"
            + " CONVERT(varchar(10), MAX(dbill_date), 23) AS gl_max_date,"
            + " CHECKSUM_AGG(CHECKSUM(ccode, iperiod)) AS gl_checksum,"
            + " CHECKSUM_AGG(BINARY_CHECKSUM(i_id, ccode, iperiod, md, mc, ibook, iflag)) AS gl_content_checksum"
            + " FROM GL_accvouch WHERE iyear = ?) g";
        static readonly string[] MarkInts = new string[]
        {
            "gl_lines", "gl_posted", "gl_void", "gl_checksum", "gl_content_checksum", "gl_max_id", "ia_count", "ia_max_id",
            "ar_count", "ar_max_id", "ap_count", "ap_max_id"
        };
        static readonly string[] MarkTexts = new string[]
        {
            "gl_max_date", "code_ufts", "close_ufts", "sa_invoice_ufts", "pu_invoice_ufts",
            "purchase_in_ufts", "product_in_ufts", "note_ufts", "ia_max_date"
        };

        public static ApiResult Run(WorkContext ctx, ReportArgs a)
        {
            int y = a.FiscalYear;
            Dictionary<string, object> body = Reports.Body();
            body["fiscal_year"] = y;
            body["modules"] = ModuleStarts(ctx);
            body["periods"] = Periods(ctx, y);
            Dictionary<string, object> mark = Rows.One(ctx.Conn, MarkSql, new object[] { y });
            body["watermarks"] = Marks(mark);
            AddPerm(body, PermCheck.Of(ctx));
            return ApiResult.Ok(body);
        }

        // perm_fingerprint：登录操作员的权限指纹（与 perm/snapshot 的 fingerprint 同一算法、同一份 60 秒快照）。
        // API 层的经营查询缓存键摘要整个 meta，操作员的功能权限或数据权限一变，缓存即失效，不会在缓存期内越权。
        internal static void AddPerm(Dictionary<string, object> body, PermContext p)
        {
            body["perm_fingerprint"] = PermSnapshot.FingerprintOf(p);
        }

        internal static string StartSql()
        {
            List<string> parts = new List<string>();
            for (int i = 0; i < StartKeys.Length; i++)
            {
                parts.Add("(cSysID = N'" + StartKeys[i][0] + "' AND cName = N'" + StartKeys[i][1] + "')");
            }
            return "SELECT cSysID AS sys, LEFT(LTRIM(RTRIM(ISNULL(cValue, N''))), 10) AS v FROM AccInformation WHERE "
                + string.Join(" OR ", parts.ToArray());
        }

        // 启用日期能解析成 yyyy-MM-dd 才算启用（未启用的模块值为空串或 NULL）。
        static Dictionary<string, object> ModuleStarts(WorkContext ctx)
        {
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, StartSql(), new object[0], StartKeys.Length * 2);
            Dictionary<string, object> modules = new Dictionary<string, object>();
            for (int i = 0; i < StartKeys.Length; i++)
            {
                string start = null;
                for (int r = 0; r < rows.Count; r++)
                {
                    DateTime day;
                    string text = GlSql.Col(rows[r], "v");
                    if (GlSql.Col(rows[r], "sys") == StartKeys[i][0] && DateTime.TryParseExact(text, "yyyy-MM-dd",
                        CultureInfo.InvariantCulture, DateTimeStyles.None, out day))
                    {
                        start = text;
                    }
                }
                Dictionary<string, object> m = new Dictionary<string, object>();
                m["enabled"] = start != null;
                m["start_date"] = start;
                modules[StartKeys[i][0]] = m;
            }
            return modules;
        }

        static List<object> Periods(WorkContext ctx, int year)
        {
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, CloseSql, new object[] { year }, 12);
            List<object> gl = ReportsMgmtPnl.Periods(ctx, year, 1, 12);
            List<object> list = new List<object>(rows.Count);
            for (int i = 0; i < rows.Count; i++)
            {
                int period = GlSql.Int(rows[i], "iperiod");
                Dictionary<string, object> closed = new Dictionary<string, object>();
                for (int m = 0; m < Modules.Length; m++)
                {
                    closed[Modules[m]] = GlSql.Int(rows[i], Modules[m]) != 0;
                }
                Dictionary<string, object> p = new Dictionary<string, object>();
                p["period"] = period;
                p["closed"] = closed;
                Dictionary<string, object> state = Find(gl, period);
                p["unposted"] = state == null ? 0 : state["unposted"];
                p["pl_transferred"] = state != null && (bool)state["pl_transferred"];
                list.Add(p);
            }
            return list;
        }

        static Dictionary<string, object> Find(List<object> periods, int period)
        {
            for (int i = 0; i < periods.Count; i++)
            {
                Dictionary<string, object> p = (Dictionary<string, object>)periods[i];
                if ((int)p["period"] == period)
                {
                    return p;
                }
            }
            return null;
        }

        static Dictionary<string, object> Marks(Dictionary<string, object> row)
        {
            Dictionary<string, object> marks = new Dictionary<string, object>();
            for (int i = 0; i < MarkInts.Length; i++)
            {
                string text = GlSql.Col(row, MarkInts[i]);
                marks[MarkInts[i]] = text.Length == 0 ? null : (object)GlSql.Int(row, MarkInts[i]);
            }
            for (int i = 0; i < MarkTexts.Length; i++)
            {
                marks[MarkTexts[i]] = row == null ? null : Reports.Text(row, MarkTexts[i]);
            }
            return marks;
        }
    }
}
