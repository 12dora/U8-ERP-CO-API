using System;
using System.Collections.Generic;

namespace U8Co
{
    // 成对设置下游连带的来源解析（VoucherSoContractDown 用）：每类下游单据的表体关联 SQL，和把一行表体解析成来源订单的纯判断。
    // 每行表体取原始链接（0 当缺省，NULLIF(…,0)）和它们各自找到的订单（SO_SODetails.ID），由 Resolve 决定这一行的来源订单：
    // - 发货单 / 退货单：DispatchLists.iSOsID；
    // - 销售出库单：rdrecords32.iDLsID → 发货单行 → 其 iSOsID；
    // - 销售发票：直接 SaleBillVouchs.iSOsID 与经 iDLsID 的发货单行 iSOsID 各自解析，两条都有而订单不同算冲突；
    //   两条都没有时（红冲蓝字发票）经 iSBVID 走一跳到蓝字行（不是本行），蓝字行再按直接 / 发货单两条解析，不再往下跳。
    // 链接给了却找不到行（悬空）、冲突、没有任何链接，这一行都没有来源（0），单据整张按混合来源跳过。
    // 表体行和它们找到的订单行（SO_SODetails s1–s4）在查询时都加 UPDLOCK, HOLDLOCK（范围锁到提交），发现与核对用同一组 JOIN。
    // 表名列名都是常量。
    internal static class VoucherSoContractLinks
    {
        const string Hold = " WITH (UPDLOCK, HOLDLOCK)";
        const string Nulls = "NULL AS bl, NULL AS bf, NULL AS d3, NULL AS o3, NULL AS dl3, NULL AS lf3, NULL AS d4, NULL AS o4";

        internal static readonly SoDownKind[] Kinds = new SoDownKind[]
        {
            Kind(new string[] { "dispatch", "DispatchList", "DLID", "cDLCode", "l.DLID" },
                "l.iDLsID AS line, NULLIF(l.iSOsID,0) AS d1, s1.ID AS o1, NULL AS dl, NULL AS lf, NULL AS d2, NULL AS o2, " + Nulls,
                "DispatchLists l" + Hold + " LEFT JOIN SO_SODetails s1" + Hold + " ON s1.iSOsID=NULLIF(l.iSOsID,0)",
                new string[] { "s1" }),
            Kind(new string[] { "sale_out", "rdrecord32", "ID", "cCode", "r.ID" },
                "r.AutoID AS line, NULL AS d1, NULL AS o1, NULLIF(r.iDLsID,0) AS dl, l.iDLsID AS lf, NULLIF(l.iSOsID,0) AS d2,"
                    + " s2.ID AS o2, " + Nulls,
                "rdrecords32 r" + Hold + " LEFT JOIN DispatchLists l" + Hold + " ON l.iDLsID=NULLIF(r.iDLsID,0)"
                    + " LEFT JOIN SO_SODetails s2" + Hold + " ON s2.iSOsID=NULLIF(l.iSOsID,0)",
                new string[] { "s2" }),
            Kind(new string[] { "sale_invoice", "SaleBillVouch", "SBVID", "cSBVCode", "b.SBVID" },
                "b.AutoID AS line, NULLIF(b.iSOsID,0) AS d1, s1.ID AS o1, NULLIF(b.iDLsID,0) AS dl, l.iDLsID AS lf,"
                    + " NULLIF(l.iSOsID,0) AS d2, s2.ID AS o2, NULLIF(b.iSBVID,0) AS bl, bb.AutoID AS bf, NULLIF(bb.iSOsID,0) AS d3,"
                    + " s3.ID AS o3, NULLIF(bb.iDLsID,0) AS dl3, l3.iDLsID AS lf3, NULLIF(l3.iSOsID,0) AS d4, s4.ID AS o4",
                "SaleBillVouchs b" + Hold + " LEFT JOIN SO_SODetails s1" + Hold + " ON s1.iSOsID=NULLIF(b.iSOsID,0)"
                    + " LEFT JOIN DispatchLists l" + Hold + " ON l.iDLsID=NULLIF(b.iDLsID,0)"
                    + " LEFT JOIN SO_SODetails s2" + Hold + " ON s2.iSOsID=NULLIF(l.iSOsID,0)"
                    + " LEFT JOIN SaleBillVouchs bb" + Hold + " ON bb.AutoID=NULLIF(b.iSBVID,0) AND bb.AutoID<>b.AutoID"
                    + " AND NULLIF(b.iSOsID,0) IS NULL AND NULLIF(b.iDLsID,0) IS NULL"
                    + " LEFT JOIN SO_SODetails s3" + Hold + " ON s3.iSOsID=NULLIF(bb.iSOsID,0)"
                    + " LEFT JOIN DispatchLists l3" + Hold + " ON l3.iDLsID=NULLIF(bb.iDLsID,0)"
                    + " LEFT JOIN SO_SODetails s4" + Hold + " ON s4.iSOsID=NULLIF(l3.iSOsID,0)",
                new string[] { "s1", "s2", "s3", "s4" }),
        };

        static SoDownKind Kind(string[] names, string cols, string from, string[] refs)
        {
            SoDownKind k = new SoDownKind();
            k.Type = names[0];
            k.Table = names[1];
            k.Key = names[2];
            k.CodeCol = names[3];
            k.HeadKey = names[4];
            k.Cols = cols;
            k.From = from;
            k.Refs = refs;
            return k;
        }

        // 发现：表体有任一条链接解析到这张订单的单据主键（参数：订单 ID，每个 Refs 一个）。
        internal static string FindSql(SoDownKind kind)
        {
            List<string> conds = new List<string>();
            foreach (string alias in kind.Refs)
            {
                conds.Add(alias + ".ID=?");
            }
            return "SELECT DISTINCT " + kind.HeadKey + " AS id FROM " + kind.From + " WHERE ("
                + string.Join(" OR ", conds.ToArray()) + ") ORDER BY " + kind.HeadKey;
        }

        internal static object[] FindArgs(SoDownKind kind, int soId)
        {
            object[] args = new object[kind.Refs.Length];
            for (int i = 0; i < args.Length; i++)
            {
                args[i] = soId;
            }
            return args;
        }

        // 一张单据的全部表体行及其链接（参数：单据主键）。
        internal static string LinesSql(SoDownKind kind)
        {
            return "SELECT " + kind.Cols + " FROM " + kind.From + " WHERE " + kind.HeadKey + "=? ORDER BY line";
        }

        internal static SoDownLine Line(Dictionary<string, object> row)
        {
            SoDownLine x = new SoDownLine();
            x.D1 = GlSql.Int(row, "d1");
            x.O1 = GlSql.Int(row, "o1");
            x.Dl = GlSql.Int(row, "dl");
            x.Lf = GlSql.Int(row, "lf");
            x.D2 = GlSql.Int(row, "d2");
            x.O2 = GlSql.Int(row, "o2");
            x.Bl = GlSql.Int(row, "bl");
            x.Bf = GlSql.Int(row, "bf");
            x.D3 = GlSql.Int(row, "d3");
            x.O3 = GlSql.Int(row, "o3");
            x.Dl3 = GlSql.Int(row, "dl3");
            x.Lf3 = GlSql.Int(row, "lf3");
            x.D4 = GlSql.Int(row, "d4");
            x.O4 = GlSql.Int(row, "o4");
            return x;
        }

        // 一行表体的来源订单：>0 是订单 ID，0 是没有来源（无链接、悬空、冲突）。蓝字一跳只在直接和发货单两条都没有时走。
        internal static int Resolve(SoDownLine x)
        {
            int own = Hop(x.D1, x.O1, x.Dl, x.Lf, x.D2, x.O2);
            if (own != 0)
            {
                return own > 0 ? own : 0;
            }
            if (x.Bl <= 0 || x.Bf <= 0)
            {
                return 0;
            }
            int blue = Hop(x.D3, x.O3, x.Dl3, x.Lf3, x.D4, x.O4);
            return blue > 0 ? blue : 0;
        }

        // 直接链接与发货单链接各自解析：>0 订单，0 两条都没给，-1 悬空或冲突。发货单行在但没有订单链接也算悬空。
        internal static int Hop(int direct, int directOrder, int dl, int dlFound, int dlLine, int dlOrder)
        {
            int a = Link(direct, directOrder);
            int b = Via(dl, dlFound, dlLine, dlOrder);
            if (a == 0 || b == 0)
            {
                return a == 0 ? b : a;
            }
            return a > 0 && a == b ? a : -1;
        }

        // 经发货单行：没给 0；发货单行找不到、或它没有订单链接、或订单行找不到，-1。
        static int Via(int dl, int dlFound, int dlLine, int dlOrder)
        {
            if (dl <= 0)
            {
                return 0;
            }
            return dlFound > 0 && dlLine > 0 && dlOrder > 0 ? dlOrder : -1;
        }

        // 一条链接：没给 0；给了且找到订单为订单 ID；给了没找到 -1。
        static int Link(int given, int order)
        {
            if (given <= 0)
            {
                return 0;
            }
            return order > 0 ? order : -1;
        }

        // 全部表体行的来源订单：升序、去重（0 为无来源）；mentions 是有没有行指向 soId。
        internal static List<int> SourcesOf(List<SoDownLine> lines, int soId, out bool mentions)
        {
            mentions = false;
            SortedSet<int> ids = new SortedSet<int>();
            foreach (SoDownLine x in lines)
            {
                ids.Add(Resolve(x));
                mentions = mentions || Mentions(x, soId);
            }
            return new List<int>(ids);
        }

        internal static bool SameIds(List<int> a, List<int> b)
        {
            if (a.Count != b.Count)
            {
                return false;
            }
            for (int i = 0; i < a.Count; i++)
            {
                if (a[i] != b[i])
                {
                    return false;
                }
            }
            return true;
        }

        // 这一行是否（经任一条链接）指向这张订单：发现和锁后复查用，冲突的行也算指向（随后按混合来源跳过）。
        internal static bool Mentions(SoDownLine x, int soId)
        {
            return x.O1 == soId || x.O2 == soId || x.O3 == soId || x.O4 == soId;
        }
    }

    // 一行表体的原始链接（0 为缺省）与各自找到的订单 / 行：D 直接链接的订单行、O 它所属订单；Dl 发货单行链接、Lf 找到的发货单行、
    // D2 / O2 发货单行的订单行和订单；Bl 蓝字行链接、Bf 找到的蓝字行，D3 / O3、Dl3 / Lf3、D4 / O4 是蓝字行的同样三组。
    internal sealed class SoDownLine
    {
        public int D1;
        public int O1;
        public int Dl;
        public int Lf;
        public int D2;
        public int O2;
        public int Bl;
        public int Bf;
        public int D3;
        public int O3;
        public int Dl3;
        public int Lf3;
        public int D4;
        public int O4;
    }

    // 下游单据种类：响应里的 type、表头表、主键列、单号列，表体关联（选列、FROM 与 JOIN、表体里的表头主键、指向订单的别名）。
    internal sealed class SoDownKind
    {
        public string Type;
        public string Table;
        public string Key;
        public string CodeCol;
        public string HeadKey;
        public string Cols;
        public string From;
        public string[] Refs;
    }
}
