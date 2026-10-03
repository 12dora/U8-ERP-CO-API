using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 期初结存导入后回读到的新单与请求行的一一对应（纯函数，--selftest 覆盖）。新单按 ID 次序、每张一行；
    // 张数相同，仓库、存货、数量（按存货数量小数位舍入）、单价（6 位）、金额（2 位），以及调用方送了的批号、货位、
    // 自由项、备注都相同才算对上，否则返回 null（调用方 504）。
    internal static class StockOpeningMatch
    {
        // 送了才比的文字字段：storeqc 标签与 rdrecord34 / rdrecords34 列同名（不分大小写）。
        static readonly string[] Texts = new string[]
        {
            "cbatch", "cposition", "cmemo", "cfree1", "cfree2", "cfree3", "cfree4", "cfree5", "cfree6", "cfree7", "cfree8",
            "cfree9", "cfree10"
        };

        internal static List<Dictionary<string, object>> Match(List<OpeningEntry> entries, List<Dictionary<string, object>> found,
            int digits)
        {
            if (found == null || found.Count != entries.Count || Distinct(found) != found.Count)
            {
                return null;
            }
            List<Dictionary<string, object>> docs = new List<Dictionary<string, object>>();
            for (int i = 0; i < entries.Count; i++)
            {
                if (!SameNumbers(entries[i], found[i], digits) || !SameTexts(entries[i], found[i]))
                {
                    return null;
                }
                docs.Add(Doc(entries[i], found[i]));
            }
            return docs;
        }

        static Dictionary<string, object> Doc(OpeningEntry e, Dictionary<string, object> row)
        {
            Dictionary<string, object> doc = new Dictionary<string, object>();
            doc["id"] = CoRows.AsId(Cell(row, "ID"));
            doc["code"] = Cell(row, "cCode");
            doc["line"] = e.Line;
            doc["wh"] = e.Wh;
            doc["inv"] = e.Inv;
            doc["qty"] = e.Qty;
            return doc;
        }

        static bool SameNumbers(OpeningEntry e, Dictionary<string, object> row, int digits)
        {
            decimal qty;
            if (CoRows.AsId(Cell(row, "ID")) <= 0 || !Dec(row, "iQuantity", out qty)
                || decimal.Round(qty, digits) != decimal.Round(e.Qty, digits))
            {
                return false;
            }
            if (!e.HasCost)
            {
                return true;
            }
            decimal cost;
            decimal price;
            return Dec(row, "iUnitCost", out cost) && Dec(row, "iPrice", out price)
                && StockUnits.Round6(cost) == StockUnits.Round6(e.Cost) && StockUnits.Round2(price) == StockUnits.Round2(e.Price);
        }

        static bool SameTexts(OpeningEntry e, Dictionary<string, object> row)
        {
            if (!Same(Cell(row, "cInvCode"), e.Inv) || !Same(Cell(row, "cWhCode"), e.Wh))
            {
                return false;
            }
            for (int i = 0; i < Texts.Length; i++)
            {
                string sent;
                if (e.Tags.TryGetValue(Texts[i], out sent) && !Same(Cell(row, Texts[i]), sent))
                {
                    return false;
                }
            }
            return true;
        }

        static bool Dec(Dictionary<string, object> row, string name, out decimal value)
        {
            return decimal.TryParse(Cell(row, name), NumberStyles.Float, CultureInfo.InvariantCulture, out value);
        }

        static bool Same(string a, string b)
        {
            return string.Equals((a ?? "").Trim(), (b ?? "").Trim(), StringComparison.OrdinalIgnoreCase);
        }

        static string Cell(Dictionary<string, object> row, string name)
        {
            return StockMsg.Col(row, name);
        }

        static int Distinct(List<Dictionary<string, object>> found)
        {
            HashSet<string> ids = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i < found.Count; i++)
            {
                ids.Add(Cell(found[i], "ID"));
            }
            return ids.Count;
        }

        internal static string IdList(List<Dictionary<string, object>> found)
        {
            List<string> ids = new List<string>();
            for (int i = 0; found != null && i < found.Count; i++)
            {
                ids.Add(Cell(found[i], "ID"));
            }
            return string.Join(",", ids.ToArray());
        }
    }
}
