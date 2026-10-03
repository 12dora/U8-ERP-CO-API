using System;
using System.Collections.Generic;

namespace U8Co
{
    // arap/process/list 的摘要（digest=true）：按（处理方式、处理号）汇总指定期间的处理行，每批给出最小 / 最大 Auto_ID、
    // 凭证号（cPZid，未制单为 null）、原币借贷合计、行数、会计年度和往来单位。调用方逐轮比对，发现新增、取消（批次消失）、
    // 制单与取消制单。期间：给了 periods 就是 fiscal_year（缺省登录年度）的这些期间；否则是该侧（应收 / 应付）全部未结账期间
    // （给了 fiscal_year 时只取该年度）。期间按会计年度（ProcListSql.Fy）和往来明细的 iPeriod 认。
    // 计提坏账 9F 只写坏账准备参数 Ar_BadPara（没有往来明细），应收的摘要在最后一页末尾另列（ArapProcListBad），不参与游标。
    internal static class ArapProcDigest
    {
        const char Sep = '\u001f';
        // 补查往来单位：每条 SQL 的批次数，与结果行数上限（超过报错，不截断）。
        const int PartnerChunk = 100;
        const int PartnerRows = 100000;

        public static ApiResult Run(WorkContext ctx, ProcListArgs a, List<int[]> states, Dictionary<string, object> body)
        {
            List<int[]> periods = Periods(a, states, ArapProcListReq.LoginYear(ctx.Item));
            List<object> used = new List<object>();
            foreach (int[] p in periods)
            {
                used.Add(ArapProcList.Period(p));
            }
            body["digest"] = true;
            body["periods"] = used;
            List<object> items = new List<object>();
            body["items"] = items;
            body["next"] = null;
            if (periods.Count == 0)
            {
                return ApiResult.Ok(body);
            }
            List<object> args = new List<object>();
            string sql = ProcListSql.DigestSql(ctx, a, periods, args);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, args.ToArray(), a.Limit + 1);
            int take = Math.Min(rows.Count, a.Limit);
            if (take > 0)
            {
                Dictionary<string, List<string>> partners = Partners(ctx, a, periods, rows, take);
                for (int i = 0; i < take; i++)
                {
                    items.Add(Item(a.Flag, rows[i], partners));
                }
            }
            if (rows.Count > a.Limit)
            {
                body["next"] = Reports.Cursor(GlSql.Col(rows[take - 1], "style"), GlSql.Col(rows[take - 1], "code"));
            }
            else if (a.Flag == "AR")
            {
                // 计提坏账 9F 没有往来明细：最后一页末尾追加坏账准备参数上的批次（ArapProcListBad）。
                ArapProcListBad.Append(ctx.Conn, periods, items);
            }
            return ApiResult.Ok(body);
        }

        // { 年度, 期间 } 按年度、期间升序（ProcListSql.DigestSql 按年度分组拼条件，要求同一年度相邻）。
        internal static List<int[]> Periods(ProcListArgs a, List<int[]> states, int loginYear)
        {
            List<int[]> list = new List<int[]>();
            if (a.Periods != null)
            {
                int year = a.FiscalYear > 0 ? a.FiscalYear : loginYear;
                foreach (int p in a.Periods)
                {
                    list.Add(new int[] { year, p });
                }
                return list;
            }
            foreach (int[] s in states)
            {
                if (s[2] == 0 && (a.FiscalYear == 0 || s[0] == a.FiscalYear))
                {
                    list.Add(new int[] { s[0], s[1] });
                }
            }
            return list;
        }

        // 每批的往来单位：去重个数不超过两个时直接取摘要行的最小、最大值；超过的批次按键补查（分批，不截断）。
        static Dictionary<string, List<string>> Partners(WorkContext ctx, ProcListArgs a, List<int[]> periods,
            List<Dictionary<string, object>> rows, int take)
        {
            Dictionary<string, List<string>> map = new Dictionary<string, List<string>>(StringComparer.Ordinal);
            List<string[]> more = new List<string[]>();
            for (int i = 0; i < take; i++)
            {
                List<string> pair = Pair(rows[i]);
                if (pair == null)
                {
                    more.Add(KeyOf(rows[i]));
                    continue;
                }
                map[Join(KeyOf(rows[i]))] = pair;
            }
            for (int i = 0; i < more.Count; i += PartnerChunk)
            {
                List<string[]> keys = more.GetRange(i, Math.Min(PartnerChunk, more.Count - i));
                List<object> args = new List<object>();
                string sql = ProcListSql.PartnerSql(ctx, a, periods, keys, args);
                List<Dictionary<string, object>> found = Rows.Query(ctx.Conn, sql, args.ToArray(), PartnerRows + 1);
                if (found.Count > PartnerRows)
                {
                    throw new BridgeException(500, "internal", "处理批次的往来单位超过 " + PartnerRows + " 个，请缩小期间或每页批数");
                }
                AddFound(map, found);
            }
            return map;
        }

        // 摘要行的往来单位（不含空串）；去重个数超过两个返回 null（要补查）。个数为 1 时只取最大值（不区分大小写的排序规则下
        // 最小、最大可能只差大小写）。
        internal static List<string> Pair(Dictionary<string, object> r)
        {
            int count = ProcListSql.Int(GlSql.Col(r, "p_count"));
            if (count > 2)
            {
                return null;
            }
            List<string> list = new List<string>();
            string max = GlSql.Col(r, "p_max");
            string min = GlSql.Col(r, "p_min");
            if (max.Length > 0)
            {
                list.Add(max);
            }
            if (count == 2 && min.Length > 0 && !string.Equals(min, max, StringComparison.Ordinal))
            {
                list.Add(min);
            }
            return list;
        }

        static void AddFound(Dictionary<string, List<string>> map, List<Dictionary<string, object>> found)
        {
            foreach (Dictionary<string, object> r in found)
            {
                string code = GlSql.Col(r, "partner");
                if (code.Length == 0)
                {
                    continue;
                }
                string key = Join(KeyOf(r));
                List<string> list;
                if (!map.TryGetValue(key, out list))
                {
                    list = new List<string>();
                    map[key] = list;
                }
                if (!list.Contains(code))
                {
                    list.Add(code);
                }
            }
        }

        static Dictionary<string, object> Item(string flag, Dictionary<string, object> r, Dictionary<string, List<string>> partners)
        {
            string[] key = KeyOf(r);
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["flag"] = flag;
            item["style"] = key[0];
            item["code"] = key[1];
            item["min_id"] = ProcListSql.Int(GlSql.Col(r, "min_id"));
            item["max_id"] = ProcListSql.Int(GlSql.Col(r, "max_id"));
            string pz = GlSql.Col(r, "pz");
            item["pz"] = pz.Length == 0 ? null : pz;
            item["sum_d_f"] = GlSql.Col(r, "sum_d_f");
            item["sum_c_f"] = GlSql.Col(r, "sum_c_f");
            item["rows"] = ProcListSql.Int(GlSql.Col(r, "row_count"));
            item["fiscal_year"] = ProcListSql.Int(GlSql.Col(r, "fiscal_year"));
            List<string> found;
            List<string> list = partners.TryGetValue(Join(key), out found) ? new List<string>(found) : new List<string>();
            list.Sort(StringComparer.Ordinal);
            item["partners"] = list;
            return item;
        }

        static string[] KeyOf(Dictionary<string, object> r)
        {
            return new string[] { GlSql.Col(r, "style"), GlSql.Col(r, "code") };
        }

        static string Join(string[] key)
        {
            return key[0] + Sep + key[1];
        }
    }
}
