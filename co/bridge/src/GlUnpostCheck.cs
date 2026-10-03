using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 取消记账在事务里的检查（请求连接，CoTrans 已开）：读最近一次记账的范围并加锁、过闸门（都是 409 state_mismatch），
    // 冲回之后的自检（科目总账、辅助总账与已记账凭证重新汇总的结果一致，余额首尾相接）。自检不符也是 409，整个事务回滚。
    internal static class GlUnpostCheck
    {
        // 受影响的科目：本批分录的科目及其各级上级（同 U8 的前缀汇总）。参数：年度、期间。
        static readonly string Touched = " EXISTS (SELECT 1 FROM GL_accvouch b" + GlUnpostSql.Batch("b")
            + " WHERE b.iyear=? AND b.iperiod=? AND b.iflag IS NULL AND b.ccode LIKE c.ccode + '%')";

        // 科目总账本期发生额 = 本期已记账凭证（iflag 为空）按前缀汇总。参数：年度、（Touched 的）年度、期间，然后两组（年度、期间）×2。
        static readonly string SumSql = "SELECT TOP 1 c.ccode k FROM code c WHERE c.iyear=? AND" + Touched + " AND ("
            + Diff("md", "GL_accsum s WHERE s.iyear=? AND s.iperiod=? AND s.ccode=c.ccode", "v.ccode LIKE c.ccode + '%'") + " OR "
            + Diff("mc", "GL_accsum s WHERE s.iyear=? AND s.iperiod=? AND s.ccode=c.ccode", "v.ccode LIKE c.ccode + '%'") + ")";

        // 余额首尾相接：本期起各期 年初/期初 + 借 - 贷 = 期末，期末 = 下期期初（带方向）。参数：年度、期间，Touched 的年度、期间。
        const string Signed = "(CASE WHEN a.cendd_c=N'贷' THEN -a.me ELSE a.me END)";
        static readonly string ChainSql = "SELECT TOP 1 a.ccode + N' ' + CONVERT(varchar(4), a.iperiod) k FROM GL_accsum a"
            + " WHERE a.iyear=? AND a.iperiod>=? AND a.iperiod<=12 AND"
            + Touched.Replace("c.ccode", "a.ccode")
            + " AND (ABS((CASE WHEN a.cbegind_c=N'贷' THEN -a.mb ELSE a.mb END)+ISNULL(a.md,0)-ISNULL(a.mc,0)-" + Signed + ")>0.005"
            + " OR EXISTS (SELECT 1 FROM GL_accsum n WHERE n.iyear=a.iyear AND n.ccode=a.ccode AND ISNULL(n.cexch_name,N'')=ISNULL(a.cexch_name,N'')"
            + " AND n.iperiod=a.iperiod+1 AND n.iperiod<=12 AND ABS(" + Signed + "-(CASE WHEN n.cbegind_c=N'贷' THEN -n.mb ELSE n.mb END))>0.005))";

        static string Diff(string col, string table, string match)
        {
            return "ABS(ISNULL((SELECT SUM(s." + col + ") FROM " + table + "),0)-ISNULL((SELECT SUM(v." + col + ") FROM GL_accvouch v"
                + " WHERE v.iyear=? AND v.iperiod=? AND v.ibook=1 AND v.iflag IS NULL AND " + match + "),0))>0.005";
        }

        // 辅助总账 / 多辅助总账本期发生额（按末级科目合计）= 该科目本期已记账凭证。参数同 SumSql。
        static string AssSql(string table, string flags)
        {
            string from = table + " s WHERE s.iyear=? AND s.iperiod=? AND s.ccode=c.ccode";
            return "SELECT TOP 1 c.ccode k FROM code c WHERE c.iyear=? AND " + flags.Replace("k.", "c.")
                + " AND" + Touched.Replace("LIKE c.ccode + '%'", "=c.ccode") + " AND ("
                + Diff("md", from, "v.ccode=c.ccode") + " OR " + Diff("mc", from, "v.ccode=c.ccode") + ")";
        }

        // 读最近一次记账的范围（已加锁）。没有、跨期间、超过上限都是 409。
        public static void Batch(object conn, GlUnpostAsk ask)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, GlUnpostSql.BatchSql, new object[] { ask.Year }, GlUnpostReq.Max + 1);
            if (rows.Count == 0)
            {
                throw GlState.Refuse(ask.Year.ToString(CultureInfo.InvariantCulture) + " 年度没有可恢复的记账（GL_mpostcond 为空：本年还没记过账，"
                    + "或最近一次记账已恢复）");
            }
            if (rows.Count > GlUnpostReq.Max)
            {
                throw GlState.Refuse("最近一次记账超过 " + GlUnpostReq.Max.ToString(CultureInfo.InvariantCulture) + " 张凭证，请在 U8 客户端恢复记账前状态");
            }
            int period = 0;
            foreach (Dictionary<string, object> row in rows)
            {
                GlUnpostItem item = new GlUnpostItem();
                item.Period = GlSql.Int(row, "p");
                item.Seq = GlSql.Int(row, "s");
                item.No = GlSql.Int(row, "n");
                item.Sign = GlSql.Col(row, "sign");
                if (period != 0 && item.Period != period)
                {
                    throw GlState.Refuse("最近一次记账跨多个期间，请在 U8 客户端恢复记账前状态");
                }
                period = item.Period;
                ask.Batch.Add(item);
            }
            GlUnpostReq.Match(ask, period);
            ask.Period = period;
        }

        // 期间未结账、后续期间也未结账；本批凭证都在、都已记账、没有银行对账 / 往来两清 / 往来对账标记、没有被红字冲销、
        // 没有人正在 U8 里编辑。先给本批分录加锁。
        public static void Gates(object conn, GlUnpostAsk ask)
        {
            object[] yp = new object[] { ask.Year, ask.Period };
            string flag = Rows.Scalar(conn, GlUnpostSql.PeriodSql, yp);
            if (flag == null)
            {
                throw GlState.Refuse("总账没有 " + ask.Year.ToString(CultureInfo.InvariantCulture) + " 年第 " + ask.Period.ToString(CultureInfo.InvariantCulture) + " 期");
            }
            Must(flag.Trim() == "0", "该期间总账已结账，不能取消记账");
            string later = Rows.Scalar(conn, GlUnpostSql.LaterClosedSql, yp);
            Must(later == null, "后续期间（" + (later ?? "").Trim() + " 期）总账已结账，不能取消记账");
            Rows.Scalar(conn, GlUnpostSql.LockRowsSql, yp);
            None(conn, GlUnpostSql.MissingSql, new object[] { ask.Year }, "最近一次记账的凭证已不存在：");
            None(conn, GlUnpostSql.UnpostedSql, yp, "最近一次记账的凭证已不是记账状态（可能已在 U8 里恢复）：");
            None(conn, GlUnpostSql.MarkedSql, yp, "凭证已做银行对账、往来两清或往来对账，请先在 U8 里取消：");
            None(conn, GlUnpostSql.ReversedSql, yp, "凭证已被红字冲销，不能取消记账：");
            None(conn, GlUnpostSql.EditingSql, new object[] { ask.Year }, "凭证正被其他人编辑：");
        }

        // 冲回、清标志之后、删 GL_mpostcond 之前调用：凭证标志全部改回，三张总账与重新汇总一致。
        public static void After(object conn, GlUnpostAsk ask)
        {
            int y = ask.Year;
            int p = ask.Period;
            string left = Rows.Scalar(conn, GlUnpostSql.LeftSql, new object[] { y, p });
            Must(left != null && left.Trim() == "0", "凭证的记账标志没有全部改回");
            object[] sum = new object[] { y, y, p, y, p, y, p, y, p, y, p };
            Clean(conn, SumSql, sum, "科目总账发生额");
            Clean(conn, ChainSql, new object[] { y, p, y, p }, "科目总账余额");
            Clean(conn, AssSql("GL_accass", GlUnpostSql.AssFlags), sum, "辅助总账发生额");
            Clean(conn, AssSql("GL_AccMultiAss", GlUnpostSql.MultiFlags()), sum, "多辅助总账发生额");
        }

        internal static string SumText()
        {
            return SumSql;
        }

        internal static string ChainText()
        {
            return ChainSql;
        }

        internal static string AssText(bool many)
        {
            return many ? AssSql("GL_AccMultiAss", GlUnpostSql.MultiFlags()) : AssSql("GL_accass", GlUnpostSql.AssFlags);
        }

        static void None(object conn, string sql, object[] args, string message)
        {
            string hit = Rows.Scalar(conn, sql, args);
            if (hit != null)
            {
                throw GlState.Refuse(message + hit.Trim() + "，未恢复");
            }
        }

        static void Clean(object conn, string sql, object[] args, string what)
        {
            string hit = Rows.Scalar(conn, sql, args);
            if (hit != null)
            {
                throw GlState.Refuse("取消记账后核对不符：" + what + "与已记账凭证不一致（科目 " + hit.Trim() + "），已回滚，未恢复");
            }
        }

        static void Must(bool ok, string message)
        {
            if (!ok)
            {
                throw GlState.Refuse(message);
            }
        }
    }
}
