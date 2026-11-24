using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 物料清单子件行上的一条替代料（bom_opcomponentsub）：优先级、替代存货、替代系数、是否替换、生效 / 失效日期。
    // 日期为空表示调用方没给，BomSubs.Settle 取子件行的起止日期。
    internal sealed class BomSub
    {
        public int Seq = 1;
        public string InvCode = "";
        public decimal Factor = 1m;
        public int Replace;
        public string EffBeg = "";
        public string EffEnd = "";

        public bool SameAs(BomSub want)
        {
            return Seq == want.Seq && string.Equals(InvCode.Trim(), want.InvCode.Trim(), StringComparison.OrdinalIgnoreCase)
                && Factor == want.Factor && Replace == want.Replace && EffBeg == want.EffBeg && EffEnd == want.EffEnd;
        }
    }

    // 子件行上的替代料和定位符（bom_opcomponentloc）：读、补缺省、查存货、写后核对。送给 U8 在 BomCom。
    // U8 BomBase._AddBomDetails：差异更新时替代料按物料（DInvCode）对上已有行、定位符按 DLoc 对上，没送的都删掉，
    // 所以修改时桥把库里现有的原样重送（保留）；调用方在行上给了 substitutes / locators 才换成给的（空数组为清空）。
    // 替代料的表体自定义项（Define22–37）桥不送：按物料对上的已有替代料保留原值，新加的为空。
    internal static class BomSubs
    {
        public const string SubsKey = "substitutes";
        public const string LocsKey = "locators";
        const int Cap = 100000;
        static readonly string SubsSql = "select convert(varchar(20), s.OpComponentId) as line_id,"
            + " convert(varchar(10), s.Sequence) as seq, sp.InvCode as inv_code, convert(varchar(40), s.Factor) as factor,"
            + " convert(varchar(10), isnull(s.ReplaceFlag,0)) as replace_flag, convert(varchar(10), s.EffBegDate, 23) as eff_begin,"
            + " convert(varchar(10), s.EffEndDate, 23) as eff_end" + DefineCols()
            + " from bom_opcomponentsub s join bom_opcomponent c on c.OpComponentId=s.OpComponentId"
            + " join bas_part sp on sp.PartId=s.PartId where c.BomId=? order by s.OpComponentId, s.Sequence, s.ComponentSubId";
        const string LocsSql = "select convert(varchar(20), l.OpComponentId) as line_id, convert(varchar(10), l.SortSeq) as sort_seq,"
            + " l.Loc as loc from bom_opcomponentloc l join bom_opcomponent c on c.OpComponentId=l.OpComponentId"
            + " where c.BomId=? order by l.OpComponentId, l.SortSeq, l.LocationId";

        static string DefineCols()
        {
            StringBuilder sb = new StringBuilder();
            string[][] defines = SubstituteRows.Defines;
            for (int i = 0; i < defines.Length; i++)
            {
                string col = "s.Define" + defines[i][0];
                string type = defines[i][1];
                string expr = type == "f" ? "convert(varchar(40), " + col + ", 2)" : type == "i" ? "convert(varchar(20), " + col + ")"
                    : type == "d" ? "convert(varchar(19), " + col + ", 120)" : col;
                sb.Append(", ").Append(expr).Append(" as define").Append(defines[i][0]);
            }
            return sb.ToString();
        }

        // 给 BomRead.Lines 的每行挂上 substitutes、locators（按 line_id 对上；没有时为空数组）。
        internal static void Attach(object conn, int id, List<Dictionary<string, object>> lines)
        {
            Dictionary<string, List<object>> subs = Group(Rows.Query(conn, SubsSql, new object[] { id }, Cap), true);
            Dictionary<string, List<object>> locs = Group(Rows.Query(conn, LocsSql, new object[] { id }, Cap), false);
            for (int i = 0; i < lines.Count; i++)
            {
                string key = CoRows.Col(lines[i], "line_id");
                List<object> list;
                lines[i][SubsKey] = subs.TryGetValue(key, out list) ? list : new List<object>();
                lines[i][LocsKey] = locs.TryGetValue(key, out list) ? list : new List<object>();
            }
        }

        static Dictionary<string, List<object>> Group(List<Dictionary<string, object>> rows, bool sub)
        {
            Dictionary<string, List<object>> map = new Dictionary<string, List<object>>(StringComparer.Ordinal);
            for (int i = 0; i < rows.Count; i++)
            {
                string key = CoRows.Col(rows[i], "line_id");
                List<object> list;
                if (!map.TryGetValue(key, out list))
                {
                    list = new List<object>();
                    map[key] = list;
                }
                list.Add(sub ? SubOut(rows[i]) : LocOut(rows[i]));
            }
            return map;
        }

        // 对外形状同生产订单子件的替代料（SubstituteRows）：SQL 读出的原文（日期 style 23 / 120、float style 2）交给它转换。
        static Dictionary<string, object> SubOut(Dictionary<string, object> row)
        {
            string[] cells = new string[]
            {
                CoRows.Col(row, "factor"), CoRows.Col(row, "replace_flag"), CoRows.Col(row, "eff_begin"), CoRows.Col(row, "eff_end")
            };
            Dictionary<string, object> item = SubstituteRows.Item(BomSql.Int(CoRows.Col(row, "seq")), CoRows.Col(row, "inv_code"), cells);
            SubstituteRows.AddDefines(item, delegate(string n) { return CoRows.Col(row, "define" + n); });
            return item;
        }

        static Dictionary<string, object> LocOut(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["sort_seq"] = BomSql.Int(CoRows.Col(row, "sort_seq"));
            item["loc"] = Values.Text(MfgReq.Raw(row, "loc"));
            return item;
        }

        // 库里的一行（已 Attach）→ BomRow 上的替代料、定位符（修改时原样重送）。没挂的（自检数据）为空。
        internal static void Take(BomRow row, Dictionary<string, object> line)
        {
            row.Subs = new List<BomSub>();
            row.Locs = new List<string>();
            foreach (object raw in Items(line, SubsKey))
            {
                Dictionary<string, object> item = (Dictionary<string, object>)raw;
                BomSub sub = new BomSub();
                sub.Seq = Convert.ToInt32(item["sequence"], CultureInfo.InvariantCulture);
                sub.InvCode = Values.Text(item["inv_code"]);
                sub.Factor = Convert.ToDecimal(item["factor"], CultureInfo.InvariantCulture);
                sub.Replace = Values.Flag(item["replace_flag"]) ? 1 : 0;
                sub.EffBeg = Values.Text(item["eff_begin"]);
                sub.EffEnd = Values.Text(item["eff_end"]);
                row.Subs.Add(sub);
            }
            foreach (object raw in Items(line, LocsKey))
            {
                row.Locs.Add(Values.Text(((Dictionary<string, object>)raw)["loc"]));
            }
        }

        static List<object> Items(Dictionary<string, object> line, string key)
        {
            object value;
            List<object> list = line.TryGetValue(key, out value) ? value as List<object> : null;
            return list ?? new List<object>();
        }

        // 合并出全部行之后：替代料没给的日期取子件行的起止日期（U8 ComponentSubstitute 的缺省也是子件生效日期）；
        // 生效日期晚于失效日期 400（U8 BRBas.CheckDateRange）。
        internal static void Settle(List<BomRow> rows)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                for (int k = 0; k < rows[i].Subs.Count; k++)
                {
                    BomSub sub = rows[i].Subs[k];
                    sub.EffBeg = sub.EffBeg.Length > 0 ? sub.EffBeg : rows[i].EffBeg;
                    sub.EffEnd = sub.EffEnd.Length > 0 ? sub.EffEnd : rows[i].EffEnd;
                    if (string.CompareOrdinal(sub.EffBeg, sub.EffEnd) > 0)
                    {
                        throw BomSql.Bad("第 " + BomReq.Num(rows[i].Seq) + " 行替代料 " + sub.InvCode + "：eff_begin 不能晚于 eff_end");
                    }
                }
            }
        }

        // 要查的替代料：新增行的，以及修改时带了 substitutes 的行的（原样保留的现有替代料不再查）。
        // 存货存在、允许做 BOM 子件、未停用、有不带自由项的物料（同子件规则，400）；不能是该行子件本身（U8 1011014）、
        // 不能是母件本身（U8 1020017），都是 400；母件是选项类时不能带替代料（U8 1011098，409）。parts 是 BomSql.CheckRows 的存货缓存。
        internal static void Check(object conn, BomAsk ask, List<BomRow> rows, string today, Dictionary<string, int> parts)
        {
            HashSet<int> given = Given(ask);
            bool parentChecked = false;
            for (int i = 0; i < rows.Count; i++)
            {
                if (rows[i].Subs.Count == 0 || (ask.Existing.Contains(rows[i].Seq) && !given.Contains(rows[i].Seq)))
                {
                    continue;
                }
                if (!parentChecked)
                {
                    CheckParent(conn, ask);
                    parentChecked = true;
                }
                CheckRow(conn, ask, rows[i], today, parts);
            }
        }

        // 修改里带了 substitutes 的行号。
        static HashSet<int> Given(BomAsk ask)
        {
            HashSet<int> seqs = new HashSet<int>();
            for (int i = 0; i < ask.Changes.Count; i++)
            {
                if (ask.Changes[i].Fields.ContainsKey(SubsKey))
                {
                    seqs.Add(ask.Changes[i].Seq);
                }
            }
            return seqs;
        }

        static void CheckParent(object conn, BomAsk ask)
        {
            if (BomSql.ParentIsOptionClass(conn, ask.InvCode))
            {
                throw new BridgeException(409, "state_mismatch", "母件是选项类，子件不能带替代料");
            }
        }

        static void CheckRow(object conn, BomAsk ask, BomRow row, string today, Dictionary<string, int> parts)
        {
            string at = "第 " + BomReq.Num(row.Seq) + " 行：";
            for (int k = 0; k < row.Subs.Count; k++)
            {
                string inv = row.Subs[k].InvCode;
                if (string.Equals(inv, row.InvCode, StringComparison.OrdinalIgnoreCase))
                {
                    throw BomSql.Bad(at + "替代料不能是子件本身");
                }
                if (!parts.ContainsKey(inv))
                {
                    parts[inv] = PartOf(conn, inv, today, row.Seq);
                }
                if (parts[inv] == ask.PartId)
                {
                    throw BomSql.Bad(at + "替代料不能是母件本身");
                }
            }
        }

        // 子件规则的 400 前面标上「第 n 行替代料」。
        static int PartOf(object conn, string inv, string today, int seq)
        {
            try
            {
                return BomSql.PartOf(conn, inv, false, today);
            }
            catch (BridgeException ex)
            {
                if (ex.Status != 400)
                {
                    throw;
                }
                throw BomSql.Bad("第 " + BomReq.Num(seq) + " 行替代料：" + ex.Message);
            }
        }

        // 写后核对：替代料按存货对上（同一行里不重复），优先级、系数、替换标志、起止日期都相同；定位符集合相同。
        internal static bool Same(BomRow got, BomRow want)
        {
            if (got.Subs.Count != want.Subs.Count || !SameLocs(got.Locs, want.Locs))
            {
                return false;
            }
            for (int i = 0; i < want.Subs.Count; i++)
            {
                BomSub hit = got.Subs.Find(delegate(BomSub s)
                {
                    return string.Equals(s.InvCode.Trim(), want.Subs[i].InvCode.Trim(), StringComparison.OrdinalIgnoreCase);
                });
                if (hit == null || !hit.SameAs(want.Subs[i]))
                {
                    return false;
                }
            }
            return true;
        }

        static bool SameLocs(List<string> got, List<string> want)
        {
            if (got.Count != want.Count)
            {
                return false;
            }
            List<string> a = new List<string>(got);
            List<string> b = new List<string>(want);
            a.Sort(StringComparer.Ordinal);
            b.Sort(StringComparer.Ordinal);
            for (int i = 0; i < a.Count; i++)
            {
                if (a[i] != b[i])
                {
                    return false;
                }
            }
            return true;
        }

        // 新增后核对：回读的每行（按行号顺序，与 rows 一一对应）替代料、定位符与请求相同。
        internal static bool SameAll(List<Dictionary<string, object>> lines, List<BomRow> rows)
        {
            if (lines.Count != rows.Count)
            {
                return false;
            }
            for (int i = 0; i < rows.Count; i++)
            {
                if (!Same(BomRead.RowOf(lines[i]), rows[i]))
                {
                    return false;
                }
            }
            return true;
        }
    }
}
