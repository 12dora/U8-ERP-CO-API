using System.Collections.Generic;

namespace U8Co
{
    // arap/process/list 的计提坏账（9F）：U8 计提只改坏账准备参数 Ar_BadPara 的当年行（dJtDate、iJtAmount、cCancelNo、
    // 制单后 cPZid），不写往来明细，按 Auto_ID 的明细增量里看不到。摘要（digest=true）在应收最后一页末尾追加：每个计提日期非空、
    // 有处理号的参数行一项，期间 = （iYear，计提日期的月份），只列请求期间里的；min_id / max_id / rows 为 0，sum_d_f 是 iJtAmount
    // （同年多次计提时 U8 累加在这一列、处理号换成最近一次的），往来单位为空。不参与游标（每年至多一行，不分页）。
    // 坏账发生 9G、收回 9H 是往来明细行，明细和摘要照常列出。
    internal static class ArapProcListBad
    {
        const string ParaSql = "select b.cCancelNo as code, isnull(b.cPZid,N'') as pz, {jt} as jt, convert(varchar(6), b.iYear) as y, "
            + "convert(varchar(4), month(b.dJtDate)) as p from Ar_BadPara b where b.cProcStyle=N'9F' and b.dJtDate is not null "
            + "and isnull(b.cCancelNo,N'')<>N'' order by b.iYear, b.cCancelNo";
        const int MaxRows = 1000;

        public static void Append(object conn, List<int[]> periods, List<object> items)
        {
            string sql = ParaSql.Replace("{jt}", WriteoffSql.Dec("isnull(b.iJtAmount,0)", 2));
            foreach (Dictionary<string, object> r in Rows.Query(conn, sql, new object[0], MaxRows))
            {
                Dictionary<string, object> item = Item(r, periods);
                if (item != null)
                {
                    items.Add(item);
                }
            }
        }

        // 一行参数 → 摘要项；不在请求期间里返回 null。纯函数，--selftest 用。
        internal static Dictionary<string, object> Item(Dictionary<string, object> r, List<int[]> periods)
        {
            int year = ProcListSql.Int(GlSql.Col(r, "y"));
            int period = ProcListSql.Int(GlSql.Col(r, "p"));
            if (!Contains(periods, year, period))
            {
                return null;
            }
            string pz = GlSql.Col(r, "pz");
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["flag"] = "AR";
            item["style"] = "9F";
            item["code"] = GlSql.Col(r, "code");
            item["min_id"] = 0;
            item["max_id"] = 0;
            item["pz"] = pz.Length == 0 ? null : pz;
            item["sum_d_f"] = GlSql.Col(r, "jt");
            item["sum_c_f"] = "0.00";
            item["rows"] = 0;
            item["fiscal_year"] = year;
            item["partners"] = new List<string>();
            return item;
        }

        static bool Contains(List<int[]> periods, int year, int period)
        {
            foreach (int[] p in periods)
            {
                if (p[0] == year && p[1] == period)
                {
                    return true;
                }
            }
            return false;
        }
    }
}
