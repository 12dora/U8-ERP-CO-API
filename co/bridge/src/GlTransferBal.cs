using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 一个末级科目（带辅助核算的再按辅助项）在本期的期末余额，借正贷负。
    internal sealed class GlTransferBalRow
    {
        public string Code;
        public string[] Aux = new string[6];
        public decimal Value;
    }

    // 结转取数：已记账的期末余额（同 U8：期间损益、QM() 都只取已记账，所以要求本期凭证全部记账）。
    // 不带辅助核算的末级科目取 GL_accsum.me，带辅助核算的取 GL_accass.me，按 cendd_c 定正负。
    // 核对模式（exclude_existing）再减去本期已生成的同类结转凭证及其后（同类别、凭证号更大）的结转凭证里已记账的分录。
    // 表名、列名是常量；科目编码、年度、期间、摘要只进参数。
    internal static class GlTransferBal
    {
        internal static readonly string[] AuxCols = new string[]
        {
            "cdept_id", "cperson_id", "ccus_id", "csup_id", "citem_class", "citem_id"
        };
        const string NoAux = "ISNULL(c.bperson,0)=0 AND ISNULL(c.bcus,0)=0 AND ISNULL(c.bsup,0)=0 AND ISNULL(c.bdept,0)=0"
            + " AND ISNULL(c.bitem,0)=0";
        const string Signed = "CASE x.cendd_c WHEN N'借' THEN ISNULL(x.me,0) WHEN N'贷' THEN -ISNULL(x.me,0) ELSE 0 END";
        // 已生成的同类结转凭证（t：coutsign 相同，自定义转账另比摘要）以及同期、同类别、凭证号不小于它的结转凭证（v）。
        // 参数：结转标记、是否比摘要（0/1）、摘要。
        internal const string LaterSql = "x.coutsign IN (N'" + GlTransferReq.PnlSign + "', N'" + GlTransferReq.CustomSign + "')"
            + " AND EXISTS (SELECT 1 FROM GL_accvouch t WHERE t.iyear=x.iyear AND t.iperiod=x.iperiod AND t.csign=x.csign"
            + " AND t.ino_id<=x.ino_id AND ISNULL(t.iflag,0)<>1 AND t.coutsign=? AND (?=0 OR t.cdigest=?))";
        const string ExcludedSql = "SELECT x.csign, CONVERT(varchar(12), x.ino_id) ino_id, CONVERT(varchar(12), COUNT(*)) n,"
            + " CONVERT(varchar(4), MIN(ISNULL(CONVERT(int, x.ibook),0))) posted FROM GL_accvouch x"
            + " WHERE x.iyear=? AND x.iperiod=? AND ISNULL(x.iflag,0)<>1 AND " + LaterSql + " GROUP BY x.csign, x.ino_id"
            + " ORDER BY x.csign, x.ino_id";
        const int MaxRows = 200000;

        // 期间损益：全部损益类末级科目。
        public static List<GlTransferBalRow> Pnl(object conn, GlTransferAsk ask)
        {
            return Read(conn, ask, "c.cclass=N'" + ReportsMgmtPnlSql.PlClass + "'", new object[0], "");
        }

        // 自定义转账：QM() 里出现的科目及其下级（非末级科目按下级合计）。digest 是该定义的摘要（核对模式按它认已生成的凭证）。
        public static List<GlTransferBalRow> Codes(object conn, GlTransferAsk ask, ICollection<string> codes, string digest)
        {
            StringBuilder filter = new StringBuilder("(");
            List<object> args = new List<object>();
            foreach (string code in codes)
            {
                filter.Append(args.Count == 0 ? "" : " OR ").Append("c.ccode LIKE ?");
                args.Add(code + "%");
            }
            if (args.Count == 0)
            {
                return new List<GlTransferBalRow>();
            }
            return Read(conn, ask, filter.Append(")").ToString(), args.ToArray(), digest);
        }

        static List<GlTransferBalRow> Read(object conn, GlTransferAsk ask, string filter, object[] filterArgs, string digest)
        {
            List<object> args = new List<object>();
            string sql = Sql(ask, filter, filterArgs, digest, args);
            List<GlTransferBalRow> list = new List<GlTransferBalRow>();
            foreach (Dictionary<string, object> row in Rows.Query(conn, sql, args.ToArray(), MaxRows))
            {
                GlTransferBalRow one = new GlTransferBalRow();
                one.Code = GlSql.Col(row, "ccode");
                for (int i = 0; i < AuxCols.Length; i++)
                {
                    one.Aux[i] = GlSql.Col(row, "a" + (i + 1).ToString(CultureInfo.InvariantCulture));
                }
                one.Value = GlSql.Money(row, "bal");
                list.Add(one);
            }
            return list;
        }

        internal static string Sql(GlTransferAsk ask, string filter, object[] filterArgs, string digest, List<object> args)
        {
            StringBuilder sql = new StringBuilder("SELECT u.ccode, u.a1, u.a2, u.a3, u.a4, u.a5, u.a6,");
            sql.Append(" CONVERT(varchar(40), SUM(u.v)) bal FROM (SELECT x.ccode, ").Append(Blank()).Append(", ").Append(Signed);
            sql.Append(" v FROM GL_accsum x JOIN code c ON c.iyear=x.iyear AND c.ccode=x.ccode AND c.bend=1 AND ").Append(NoAux);
            sql.Append(" WHERE x.iyear=? AND x.iperiod=? AND ").Append(filter);
            Period(args, ask, filterArgs);
            sql.Append(" UNION ALL SELECT x.ccode, ").Append(AuxSel()).Append(", ").Append(Signed);
            sql.Append(" v FROM GL_accass x JOIN code c ON c.iyear=x.iyear AND c.ccode=x.ccode AND c.bend=1 AND NOT (").Append(NoAux);
            sql.Append(") WHERE x.iyear=? AND x.iperiod=? AND ").Append(filter);
            Period(args, ask, filterArgs);
            if (ask.Exclude)
            {
                sql.Append(" UNION ALL SELECT x.ccode, ").Append(AuxSel()).Append(", ISNULL(x.mc,0)-ISNULL(x.md,0)");
                sql.Append(" v FROM GL_accvouch x JOIN code c ON c.iyear=x.iyear AND c.ccode=x.ccode AND c.bend=1");
                sql.Append(" WHERE x.iyear=? AND x.iperiod=? AND ").Append(filter);
                sql.Append(" AND ISNULL(CONVERT(int, x.ibook),0)=1 AND ISNULL(x.iflag,0)<>1 AND ").Append(LaterSql);
                Period(args, ask, filterArgs);
                args.AddRange(LaterArgs(ask, digest));
            }
            sql.Append(") u GROUP BY u.ccode, u.a1, u.a2, u.a3, u.a4, u.a5, u.a6 HAVING ROUND(SUM(u.v),2)<>0");
            sql.Append(" ORDER BY u.ccode, u.a1, u.a2, u.a3, u.a4, u.a5, u.a6");
            return sql.ToString();
        }

        static void Period(List<object> args, GlTransferAsk ask, object[] filterArgs)
        {
            args.Add(ask.Year);
            args.Add(ask.Period);
            args.AddRange(filterArgs);
        }

        internal static object[] LaterArgs(GlTransferAsk ask, string digest)
        {
            return new object[] { ask.OutSign, ask.Pnl ? 0 : 1, digest ?? "" };
        }

        static string Blank()
        {
            return "N'' a1, N'' a2, N'' a3, N'' a4, N'' a5, N'' a6";
        }

        static string AuxSel()
        {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < AuxCols.Length; i++)
            {
                sb.Append(i == 0 ? "" : ", ").Append("ISNULL(x.").Append(AuxCols[i]).Append(",N'') a")
                    .Append((i + 1).ToString(CultureInfo.InvariantCulture));
            }
            return sb.ToString();
        }

        // 核对模式从余额里去掉的凭证（[{sign, no, lines, posted}]），写进预演 detail。
        public static void Excluded(object conn, GlTransferPlan plan, string digest)
        {
            GlTransferAsk ask = plan.Ask;
            object[] args = GlSql.With(new object[] { ask.Year, ask.Period }, LaterArgs(ask, digest));
            foreach (Dictionary<string, object> row in Rows.Query(conn, ExcludedSql, args, 1000))
            {
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["sign"] = GlSql.Col(row, "csign");
                one["no"] = GlSql.Int(row, "ino_id");
                one["lines"] = GlSql.Int(row, "n");
                one["posted"] = GlSql.Int(row, "posted") != 0;
                if (!ask.Pnl)
                {
                    one["tran_digest"] = digest;
                }
                plan.Excluded.Add(one);
            }
        }

        // 按科目要的辅助维度归并（不要的维度合在一起）：键 → 余额。下标同 AuxCols。
        public static Dictionary<string, GlTransferBalRow> Group(List<GlTransferBalRow> rows, GlTransferCodes codes)
        {
            Dictionary<string, GlTransferBalRow> map = new Dictionary<string, GlTransferBalRow>(StringComparer.OrdinalIgnoreCase);
            foreach (GlTransferBalRow row in rows)
            {
                bool[] dims = codes.Dims(row.Code);
                GlTransferBalRow one = new GlTransferBalRow();
                one.Code = row.Code;
                for (int i = 0; i < AuxCols.Length; i++)
                {
                    one.Aux[i] = dims[i] ? row.Aux[i] : "";
                }
                string key = one.Code + "\n" + string.Join("\n", one.Aux);
                GlTransferBalRow have;
                if (map.TryGetValue(key, out have))
                {
                    have.Value += row.Value;
                    continue;
                }
                one.Value = row.Value;
                map[key] = one;
            }
            return map;
        }
    }
}
