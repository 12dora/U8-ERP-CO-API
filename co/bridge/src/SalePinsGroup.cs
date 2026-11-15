using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 销售订单表体的计量组（数量 iQuantity、件数 iNum、换算率 iInvExchRate、计量单位 cUnitID）核对。
    // 计量组可能变的行：新增订单的每一行、修改的 op=add 行，以及 op=update 行带了数量、件数、换算率、单位、单位组或存货。
    // 保存前（400）：同时给了数量、件数、换算率而三者不成一套。保存后、提交前（同一事务，409 u8_rejected，调用方回滚）：
    // 请求给了的数量、件数、换算率必须原样落库（不许被 U8 按别的键算掉）；有换算的存货单位须属于存货的计量单位组，
    // 换算率大于 0，固定换算（iGroupType=1）时等于单位档案的换算率，固定与浮动（2）都要 件数 × 换算率 = 数量。
    // 请求明确给了其中两个或三个：|数量 − 件数 × 换算率| ≤ 0.5 × 10^(−数量小数位)，无额外容差（不成一套保存前 400）；
    // 只给了一个、由组件算出另一个：按被算字段小数位、AwayFromZero 舍入后与落库值相等（不容差）。桥不改这些列。
    internal sealed class SaleQty
    {
        internal decimal Qty;
        internal decimal Num;
        internal decimal Rate;
        internal int GroupType;
        internal decimal UnitRate;
        internal bool InGroup;
    }

    // 请求里一行的计量组：Qty / Num / Rate 是请求给的值（没给为 null），Key 是 iSOsID（新增行保存后由 AssignKeys 填）。
    internal sealed class SaleQtyLine
    {
        internal string Path;
        internal int Key;
        internal int Line = -1;
        internal string Inv = "";
        internal string Qty;
        internal string Num;
        internal string Rate;
    }

    // 账套小数位：数量 iStrsQuanDecDgt、件数 iNumDecDgt（读不到按 U8 缺省 2 / 4）。
    internal sealed class SaleDigits
    {
        internal int Qty = 2;
        internal int Num = 4;
    }

    internal sealed partial class SalePins
    {
        const string GroupSql = "SELECT d.iQuantity AS q, d.iNum AS n, d.iInvExchRate AS r, ISNULL(i.iGroupType,0) AS gt,"
            + " u.iChangRate AS ur, CASE WHEN u.cGroupCode = i.cGroupCode THEN '1' ELSE '0' END AS ing"
            + " FROM SO_SODetails d JOIN Inventory i ON i.cInvCode = d.cInvCode"
            + " LEFT JOIN ComputationUnit u ON u.cComunitCode = d.cUnitID WHERE d.iSOsID=?";
        const string DigitsSql = "SELECT cName AS n, cValue AS v FROM AccInformation WHERE cSysID='AA'"
            + " AND cName IN (N'iStrsQuanDecDgt',N'iNumDecDgt')";
        // 改了其中任一个，op=update 行的计量组就可能变。
        static readonly string[] GroupKeys = new string[] {
            "iquantity", "inum", "iinvexchrate", "cunitid", "cgroupcode", "igrouptype", "ccomunitcode", "cinvcode"
        };

        internal List<SaleQtyLine> Groups = new List<SaleQtyLine>();

        void TakeGroup(Dictionary<string, object> line, string at, bool update, int key, int slot)
        {
            if (update && !TouchesGroup(line))
            {
                return;
            }
            SaleQtyLine g = new SaleQtyLine();
            g.Path = at;
            g.Key = key;
            g.Line = slot;
            g.Inv = Text(line, "cinvcode");
            g.Qty = Sent(line, "iquantity", at);
            g.Num = Sent(line, "inum", at);
            g.Rate = Sent(line, "iinvexchrate", at);
            Groups.Add(g);
        }

        internal static bool TouchesGroup(Dictionary<string, object> line)
        {
            for (int i = 0; i < GroupKeys.Length; i++)
            {
                object value = Raw(line, GroupKeys[i]);
                if (value != null && !(value is DBNull))
                {
                    return true;
                }
            }
            return false;
        }

        static string Sent(Dictionary<string, object> line, string name, string at)
        {
            string value = Cell(Raw(line, name), FieldPath.Join(at, name));
            return value == null || value.Trim().Length == 0 ? null : value.Trim();
        }

        // 保存前：同时给了数量、件数、换算率时三者须成一套，否则 400（不交给 U8 挑一个算掉另一个）。
        void CheckSupplied(object conn)
        {
            SaleDigits dig = null;
            for (int i = 0; i < Groups.Count; i++)
            {
                SaleQtyLine g = Groups[i];
                decimal q;
                decimal n;
                decimal r;
                if (!SoUnits.Dec(g.Qty, out q) || !SoUnits.Dec(g.Num, out n) || !SoUnits.Dec(g.Rate, out r) || r <= 0m)
                {
                    continue;
                }
                if (dig == null)
                {
                    dig = Digits(conn);
                }
                if (!Fits(q, n, r, dig))
                {
                    throw BridgeException.BadField(g.Path, "数量 " + Txt(q) + " 不等于件数 " + Txt(n) + " × 换算率 " + Txt(r));
                }
            }
        }

        // 保存后：每个计量组可能变的行逐行核对。
        void CheckGroups(object conn, WorkItem item)
        {
            if (Groups.Count == 0)
            {
                return;
            }
            SaleDigits dig = Digits(conn);
            for (int i = 0; i < Groups.Count; i++)
            {
                SaleQtyLine g = Groups[i];
                Dictionary<string, object> row = g.Key <= 0 ? null : Rows.One(conn, GroupSql, new object[] { g.Key });
                string problem = row == null ? "明细行不存在" : LineProblem(g, QtyOf(row), dig);
                if (problem.Length == 0)
                {
                    continue;
                }
                CoRows.Note(item, "计量核对 " + g.Path + " " + problem);
                throw new BridgeException(409, "u8_rejected", "U8 保存后 " + g.Path + " 的计量不一致：" + problem, g.Path);
            }
        }

        static SaleDigits Digits(object conn)
        {
            SaleDigits dig = new SaleDigits();
            List<Dictionary<string, object>> rows = Rows.Query(conn, DigitsSql, new object[0], 10);
            for (int i = 0; i < rows.Count; i++)
            {
                int d;
                if (!int.TryParse(CoRows.Col(rows[i], "v").Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out d))
                {
                    continue;
                }
                d = Math.Max(0, Math.Min(d, 10));
                if (string.Equals(CoRows.Col(rows[i], "n"), "iNumDecDgt", StringComparison.OrdinalIgnoreCase))
                {
                    dig.Num = d;
                }
                else
                {
                    dig.Qty = d;
                }
            }
            return dig;
        }

        static SaleQty QtyOf(Dictionary<string, object> row)
        {
            SaleQty q = new SaleQty();
            q.Qty = Dec(CoRows.Col(row, "q"));
            q.Num = Dec(CoRows.Col(row, "n"));
            q.Rate = Dec(CoRows.Col(row, "r"));
            q.GroupType = (int)Dec(CoRows.Col(row, "gt"));
            q.UnitRate = Dec(CoRows.Col(row, "ur"));
            q.InGroup = CoRows.Col(row, "ing") == "1";
            return q;
        }

        // 纯函数（--selftest 用）：空串为一致，否则是问题描述。先看请求值是否原样落库，再看整组。
        internal static string LineProblem(SaleQtyLine g, SaleQty q, SaleDigits dig)
        {
            if (!Held(g.Qty, q.Qty))
            {
                return "数量请求 " + g.Qty + "，保存 " + Txt(q.Qty);
            }
            if (!Held(g.Num, q.Num))
            {
                return "件数请求 " + g.Num + "，保存 " + Txt(q.Num);
            }
            if (!Held(g.Rate, q.Rate))
            {
                return "换算率请求 " + g.Rate + "，保存 " + Txt(q.Rate);
            }
            string gp = GroupProblem(q, dig);
            if (gp.Length > 0 || q.GroupType == 0)
            {
                return gp;
            }
            if (ProductOk(g, q, dig))
            {
                return "";
            }
            return "数量 " + Txt(q.Qty) + " 不等于件数 " + Txt(q.Num) + " × 换算率 " + Txt(q.Rate);
        }

        // 单计量、单位组、换算率、固定换算档案；数量 = 件数 × 换算率由 LineProblem 按请求给了几个值选明确容差或舍入相等。
        internal static string GroupProblem(SaleQty q, SaleDigits dig)
        {
            if (q.GroupType == 0)
            {
                return "";
            }
            if (!q.InGroup)
            {
                return "计量单位不属于存货的计量单位组";
            }
            if (q.Rate <= 0m)
            {
                return "换算率 " + Txt(q.Rate) + " 必须大于 0";
            }
            if (q.GroupType == 1 && q.Rate != q.UnitRate)
            {
                return "换算率 " + Txt(q.Rate) + " 与计量单位的换算率 " + Txt(q.UnitRate) + " 不同";
            }
            return "";
        }

        static bool ProductOk(SaleQtyLine g, SaleQty q, SaleDigits dig)
        {
            if (SentCount(g) >= 2)
            {
                return Fits(q.Qty, q.Num, q.Rate, dig);
            }
            return FitsDerived(q, dig, g.Num == null);
        }

        static int SentCount(SaleQtyLine g)
        {
            int n = 0;
            if (g.Qty != null)
            {
                n++;
            }
            if (g.Num != null)
            {
                n++;
            }
            if (g.Rate != null)
            {
                n++;
            }
            return n;
        }

        // 请求给了两个或三个：|数量 − 件数 × 换算率| ≤ 0.5 × 10^(−数量小数位)，decimal，无额外容差。
        internal static bool Fits(decimal qty, decimal num, decimal rate, SaleDigits dig)
        {
            return Math.Abs(qty - num * rate) <= Half(dig.Qty);
        }

        // 只给一个：件数 = round(数量/换算率, 件数小数位, AwayFromZero)，或数量 = round(件数×换算率, 数量小数位)；与落库值相等。
        static bool FitsDerived(SaleQty q, SaleDigits dig, bool pieces)
        {
            if (q.Rate <= 0m)
            {
                return false;
            }
            if (pieces)
            {
                return q.Num == Math.Round(q.Qty / q.Rate, dig.Num, MidpointRounding.AwayFromZero);
            }
            return q.Qty == Math.Round(q.Num * q.Rate, dig.Qty, MidpointRounding.AwayFromZero);
        }

        static decimal Half(int dec)
        {
            return 0.5m / Pow10(dec);
        }

        static bool Held(string sent, decimal stored)
        {
            decimal v;
            return sent == null || (SoUnits.Dec(sent, out v) && v == stored);
        }

        static decimal Pow10(int dec)
        {
            decimal p = 1m;
            for (int i = 0; i < dec; i++)
            {
                p = p * 10m;
            }
            return p;
        }

        static decimal Dec(string text)
        {
            decimal v;
            return SoUnits.Dec(text, out v) ? v : 0m;
        }

        static string Txt(decimal v)
        {
            return v.ToString("0.######", CultureInfo.InvariantCulture);
        }
    }
}
