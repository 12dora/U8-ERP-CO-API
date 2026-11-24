using System;
using System.Collections.Generic;

namespace U8Co
{
    // 存货核算制单的科目（同 U8 制单 frmMakePz 的取法，生产数据回测核对）。设置表按会计年度整表读一次。
    // 存货科目：「存货科目」IA_HeadSet（cInvHead；发出商品 cOutHead）。行上填了的键都要与明细行一致（存货编码相等、
    // 存货分类按编码前缀、仓库相等），没填的不限；优先级：按存货的行 → 按存货分类的行（分类编码长的优先）→ 按仓库的行 → 全空的行，
    // 同级再比填了的键多的。只看该科目列非空的行。
    // 对方科目：「对方科目」IA_OppHead（cOppHead；暂估 cEstHead）。同样「填了的键都要一致」（收发类别、存货、存货分类前缀、部门、
    // 项目大类、项目），只看该科目列非空的行；U8 按（收发类别、存货、存货分类、部门、对象、项目、项目大类）升序排、取最后一行，
    // 即逐键比较：填了的大于没填的，都填了按编码比较（分类前缀长的大）。最大的几行完全相同、科目不同时 409，不去猜。
    internal sealed class IaVoucherAcct
    {
        const string HeadSql = "select isnull(cWhCode,N'') wh, isnull(cInvCCode,N'') invc, isnull(cInvCode,N'') inv, "
            + "isnull(cInvHead,N'') h_inv, isnull(cOutHead,N'') h_out from IA_HeadSet where iYear=?";
        const string OppSql = "select isnull(cRdCode,N'') rd, isnull(cInvCode,N'') inv, isnull(cInvCCode,N'') invc, "
            + "isnull(cDepCode,N'') dep, isnull(cObjCode,N'') obj, isnull(cItemCode,N'') itm, isnull(citem_class,N'') itc, "
            + "isnull(cOppHead,N'') o_opp, isnull(cEstHead,N'') o_est from IA_OppHead where iYear=?";
        const int SetMax = 20000;

        readonly List<Dictionary<string, object>> heads;
        readonly List<Dictionary<string, object>> opps;

        IaVoucherAcct(List<Dictionary<string, object>> heads, List<Dictionary<string, object>> opps)
        {
            this.heads = heads;
            this.opps = opps;
        }

        public static IaVoucherAcct Load(object conn, int year)
        {
            List<Dictionary<string, object>> heads = Rows.Query(conn, HeadSql, new object[] { year }, SetMax + 1);
            List<Dictionary<string, object>> opps = Rows.Query(conn, OppSql, new object[] { year }, SetMax + 1);
            if (heads.Count > SetMax || opps.Count > SetMax)
            {
                throw IaVoucherLoad.Refuse("存货科目或对方科目设置超过 " + SetMax + " 行，请在 U8 客户端制单");
            }
            return new IaVoucherAcct(heads, opps);
        }

        // 给一行定存货科目、对方科目。缺科目或多行同样匹配、科目不同时 409，消息点名单据和哪一方。
        public void Resolve(IaBill bill, IaRow row)
        {
            row.InHead = Head(row, "h_inv");
            if (row.InHead.Length == 0)
            {
                throw Missing(bill, row, "存货科目", "存货核算「存货科目」");
            }
            string opp = OppColumn(row);
            row.OppHead = opp == "h_out" ? Head(row, opp) : Opp(row, opp);
            if (row.OppHead.Length == 0)
            {
                throw Missing(bill, row, opp == "h_out" ? "发出商品科目" : "对方科目",
                    opp == "h_out" ? "存货核算「存货科目」的发出商品科目" : "存货核算「对方科目」（收发类别 " + Dash(row.Rd) + "）");
            }
        }

        // 对方科目取哪一列：采购入库单暂估记账（bFlag=1）、红字回冲单取暂估科目，销售出库单取发出商品科目，其余取对方科目。
        internal static string OppColumn(IaRow row)
        {
            switch (row.Type.Opp)
            {
                case "est":
                    return "o_est";
                case "estFlag":
                    return row.BFlag == "1" ? "o_est" : "o_opp";
                case "out":
                    return "h_out";
                default:
                    return "o_opp";
            }
        }

        string Head(IaRow row, string column)
        {
            int[] best = null;
            string value = "";
            bool clash = false;
            foreach (Dictionary<string, object> h in heads)
            {
                string v = CoRows.Col(h, column);
                int[] rank = v.Length == 0 ? null : HeadRank(h, row);
                if (rank == null)
                {
                    continue;
                }
                int cmp = best == null ? 1 : Compare(rank, best);
                if (cmp > 0)
                {
                    best = rank;
                    value = v;
                    clash = false;
                }
                else if (cmp == 0 && !v.Equals(value, StringComparison.OrdinalIgnoreCase))
                {
                    clash = true;
                }
            }
            if (clash)
            {
                throw IaVoucherLoad.Refuse("存货 " + row.Inv + " 在「存货科目」设置里有多行同样匹配、科目不同，请在 U8 里调整后再制单");
            }
            return value;
        }

        // 匹配返回 {级别（大优先：存货 3、分类 2、仓库 1、全空 0）, 分类编码长度, 填了的键数}；不匹配返回 null。
        internal static int[] HeadRank(Dictionary<string, object> h, IaRow row)
        {
            string wh = CoRows.Col(h, "wh");
            string invc = CoRows.Col(h, "invc");
            string inv = CoRows.Col(h, "inv");
            if (!Key(inv, row.Inv, false) || !Key(invc, row.InvClass, true) || !Key(wh, row.Wh, false))
            {
                return null;
            }
            // 按存货分类又按仓库的行：U8 按分类、按仓库各更新一遍，哪一遍生效取决于语句先后（未能从程序里还原），不去猜。
            if (inv.Length == 0 && invc.Length > 0 && wh.Length > 0)
            {
                throw IaVoucherLoad.Refuse("存货 " + row.Inv + " 在「存货科目」设置里匹配到按存货分类 " + invc + " 又按仓库 " + wh
                    + " 的行，U8 的取法未能核对，请在 U8 客户端制单");
            }
            return new int[] { Level(inv, invc, wh), invc.Length, Filled(inv) + Filled(invc) + Filled(wh) };
        }

        string Opp(IaRow row, string column)
        {
            string[] best = null;
            string value = "";
            bool clash = false;
            foreach (Dictionary<string, object> o in opps)
            {
                string v = CoRows.Col(o, column);
                string[] keys = v.Length == 0 ? null : OppKeys(o, row);
                if (keys == null)
                {
                    continue;
                }
                int cmp = best == null ? 1 : CompareKeys(keys, best);
                if (cmp > 0)
                {
                    best = keys;
                    value = v;
                    clash = false;
                }
                else if (cmp == 0 && !v.Equals(value, StringComparison.OrdinalIgnoreCase))
                {
                    clash = true;
                }
            }
            if (clash)
            {
                throw IaVoucherLoad.Refuse("存货 " + row.Inv + "（收发类别 " + Dash(row.Rd) + "）在「对方科目」设置里有多行同样匹配、科目不同，"
                    + "请在 U8 里调整后再制单");
            }
            return value;
        }

        // 匹配时返回 U8 的排序键（收发类别、存货、存货分类、部门、对象、项目、项目大类）；不匹配返回 null。对象 cObjCode 不参与匹配（同 U8）。
        internal static string[] OppKeys(Dictionary<string, object> o, IaRow row)
        {
            string rd = CoRows.Col(o, "rd");
            string inv = CoRows.Col(o, "inv");
            string invc = CoRows.Col(o, "invc");
            string dep = CoRows.Col(o, "dep");
            string itm = CoRows.Col(o, "itm");
            string itc = CoRows.Col(o, "itc");
            bool[] hits = new bool[]
            {
                Key(rd, row.Rd, false), Key(inv, row.Inv, false), Key(invc, row.InvClass, true), Key(dep, row.Dept, false),
                Key(itc, row.ItemClass, false), Key(itm, row.Item, false)
            };
            return Array.IndexOf(hits, false) < 0 ? new string[] { rd, inv, invc, dep, CoRows.Col(o, "obj"), itm, itc } : null;
        }

        // 逐键：没填（空串）小于填了的，都填了按不区分大小写的序数比较。
        internal static int CompareKeys(string[] a, string[] b)
        {
            for (int i = 0; i < a.Length; i++)
            {
                if (a[i].Length == 0 || b[i].Length == 0)
                {
                    int filled = (a[i].Length > 0 ? 1 : 0) - (b[i].Length > 0 ? 1 : 0);
                    if (filled != 0)
                    {
                        return filled;
                    }
                    continue;
                }
                int cmp = string.Compare(a[i], b[i], StringComparison.OrdinalIgnoreCase);
                if (cmp != 0)
                {
                    return cmp;
                }
            }
            return 0;
        }

        static int Compare(int[] a, int[] b)
        {
            for (int i = 0; i < a.Length; i++)
            {
                if (a[i] != b[i])
                {
                    return a[i] > b[i] ? 1 : -1;
                }
            }
            return 0;
        }

        // 设置行上的一个键：没填不限；填了要与明细一致（prefix 为真时按编码前缀，用于分类）。
        static bool Key(string have, string want, bool prefix)
        {
            if (have.Length == 0)
            {
                return true;
            }
            return prefix ? Prefix(have, want) : Same(have, want);
        }

        // 存货科目的级别：按存货 3、按存货分类 2、按仓库 1、全空 0。
        static int Level(string inv, string invc, string wh)
        {
            if (inv.Length > 0)
            {
                return 3;
            }
            if (invc.Length > 0)
            {
                return 2;
            }
            return wh.Length > 0 ? 1 : 0;
        }

        static int Filled(string key)
        {
            return key.Length > 0 ? 1 : 0;
        }

        static bool Same(string have, string want)
        {
            return want.Length > 0 && have.Equals(want, StringComparison.OrdinalIgnoreCase);
        }

        static bool Prefix(string have, string want)
        {
            return want.Length > 0 && want.StartsWith(have, StringComparison.OrdinalIgnoreCase);
        }

        static string Dash(string value)
        {
            return value.Length > 0 ? value : "空";
        }

        static BridgeException Missing(IaBill bill, IaRow row, string side, string where)
        {
            return IaVoucherLoad.Refuse("单据 " + bill.Label() + " 存货 " + row.Inv + "：" + side + "没有设置（" + where + "），请在 U8 里设置后再制单");
        }
    }
}
