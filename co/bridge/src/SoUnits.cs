using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 调用方没带计量单位时，按存货档案补销售单位。换算率 ≤ 0 视为未知，不写入。
    // 预发货（U8 模板必输，客户端缺省单据日期）：行上的预发货、表头预发货、本行预完工、单据日期。
    // 预完工：行上的预完工、行上的预发货、表头预完工、表头预发货、单据日期。
    internal static class SoUnits
    {
        const string InvSql = "select cGroupCode, iGroupType, cComUnitCode, cSAComUnitCode from Inventory where cInvCode=?";
        const string RateSql = "select iChangRate from ComputationUnit where cComunitCode=?";

        public static void Fill(object conn, Dictionary<string, string> fields, List<Dictionary<string, string>> lines,
            Dictionary<string, string> head)
        {
            if (lines == null)
            {
                return;
            }
            for (int i = 0; i < lines.Count; i++)
            {
                FillOne(conn, fields, lines[i], head);
            }
        }

        static void FillOne(object conn, Dictionary<string, string> fields, Dictionary<string, string> line,
            Dictionary<string, string> head)
        {
            string inv = Get(line, "cInvCode").Trim();
            if (inv.Length > 0)
            {
                Dictionary<string, object> row = Rows.One(conn, InvSql, new object[] { inv });
                if (row != null)
                {
                    ApplyInv(conn, fields, line, row);
                }
            }
            // 先按调用方给的日期定预完工（表头预完工优先于缺省的预发货），再补预发货。
            PutMissing(line, fields, "dPreMoDate", PreMo(line, head));
            PutMissing(line, fields, "dPreDate", PreDate(line, head));
        }

        // U8 要求预完工不晚于预发货：没有表头预发货时取本行已定的预完工（调用方给的或单据日期）。
        static string PreDate(Dictionary<string, string> line, Dictionary<string, string> head)
        {
            string mo = Get(line, "dPreMoDate");
            string pre = Get(head, "dPreDateBT");
            if (pre.Length > 0)
            {
                // 表头预发货早于本行预完工时取预完工。
                return mo.Length > 0 && Later(mo, pre) ? mo : pre;
            }
            return mo.Length > 0 ? mo : Get(head, "dDate");
        }

        // 按日期比较（调用方可能给 2026/10/5、2026-10-05 或带时间）；任一方解析不了时退回按字符串比较。
        static bool Later(string a, string b)
        {
            DateTime da;
            DateTime db;
            if (ParseDate(a, out da) && ParseDate(b, out db))
            {
                return da.Date > db.Date;
            }
            return string.CompareOrdinal(a, b) > 0;
        }

        static bool ParseDate(string text, out DateTime value)
        {
            string t = text.Trim();
            return DateTime.TryParse(t, CultureInfo.InvariantCulture, DateTimeStyles.None, out value)
                || DateTime.TryParse(t, CultureInfo.GetCultureInfo("zh-CN"), DateTimeStyles.None, out value);
        }

        // 新增时表头缺省（规则同无来源发货单，SrcLessReq.Exch）：不带币种或本位币时写本位币、汇率 1，另给的汇率必须是 1；
        // 外币必须给大于 0 的汇率。U8 组件不补，汇率空时报「汇率不可以小于等于0」。
        public static void FillHead(object conn, Dictionary<string, string> fields, Dictionary<string, string> head,
            Dictionary<string, object> raw)
        {
            string curKey = Canon(fields, "cexch_name");
            string rateKey = Canon(fields, "iExchRate");
            if (curKey == null || rateKey == null || head == null || raw == null)
            {
                return;
            }
            string[] exch = SrcLessReq.Exch(raw, AccDefaults.Home(conn));
            PutMissing(head, fields, curKey, exch[0]);
            PutMissing(head, fields, rateKey, exch[1]);
        }

        static string Canon(Dictionary<string, string> fields, string name)
        {
            string canon;
            if (fields == null || !fields.TryGetValue(name, out canon) || canon == null || canon.Length == 0)
            {
                return null;
            }
            return canon;
        }

        static string PreMo(Dictionary<string, string> line, Dictionary<string, string> head)
        {
            string pre = Get(line, "dPreDate");
            if (pre.Length > 0)
            {
                return pre;
            }
            pre = Get(head, "dPreMoDateBT");
            if (pre.Length > 0)
            {
                return pre;
            }
            pre = Get(head, "dPreDateBT");
            if (pre.Length > 0)
            {
                return pre;
            }
            return Get(head, "dDate");
        }

        static void ApplyInv(object conn, Dictionary<string, string> fields, Dictionary<string, string> line,
            Dictionary<string, object> row)
        {
            PutMissing(line, fields, "cGroupCode", CoRows.Col(row, "cGroupCode"));
            PutMissing(line, fields, "iGroupType", CoRows.Col(row, "iGroupType"));
            PutMissing(line, fields, "cComUnitCode", CoRows.Col(row, "cComUnitCode"));
            decimal group;
            if (Dec(Get(line, "iGroupType"), out group) && group == 0m)
            {
                line.Remove("cUnitID");
                line.Remove("iInvExchRate");
                return;
            }
            string unit = UnitOf(fields, line, row);
            string main = Get(line, "cComUnitCode");
            if (main.Length == 0)
            {
                main = CoRows.Col(row, "cComUnitCode");
            }
            ApplyRate(conn, fields, line, unit, main);
        }

        static void ApplyRate(object conn, Dictionary<string, string> fields, Dictionary<string, string> line,
            string unit, string main)
        {
            string rate = Get(line, "iInvExchRate");
            if (!Positive(rate))
            {
                line.Remove("iInvExchRate");
                rate = RateOf(conn, unit, main);
                if (Positive(rate))
                {
                    PutMissing(line, fields, "iInvExchRate", rate);
                }
                else
                {
                    rate = "";
                }
            }
            ApplyQty(fields, line, rate);
        }

        static string UnitOf(Dictionary<string, string> fields, Dictionary<string, string> line, Dictionary<string, object> row)
        {
            string unit = Get(line, "cUnitID");
            if (unit.Length > 0)
            {
                return unit;
            }
            unit = CoRows.Col(row, "cSAComUnitCode");
            if (unit.Length == 0)
            {
                unit = CoRows.Col(row, "cComUnitCode");
            }
            PutMissing(line, fields, "cUnitID", unit);
            return unit;
        }

        static string RateOf(object conn, string unit, string main)
        {
            if (unit.Length == 0)
            {
                return "";
            }
            if (string.Equals(unit.Trim(), main.Trim(), StringComparison.OrdinalIgnoreCase))
            {
                return "1";
            }
            Dictionary<string, object> row = Rows.One(conn, RateSql, new object[] { unit.Trim() });
            if (row == null)
            {
                return "";
            }
            return Known(CoRows.Col(row, "iChangRate"));
        }

        static string Known(string text)
        {
            if (!Positive(text))
            {
                return "";
            }
            return text.Trim();
        }

        static void ApplyQty(Dictionary<string, string> fields, Dictionary<string, string> line, string rateText)
        {
            decimal rate;
            if (!Dec(rateText, out rate) || rate <= 0m)
            {
                return;
            }
            decimal qty;
            decimal num;
            bool hasQty = Dec(Get(line, "iQuantity"), out qty);
            bool hasNum = Dec(Get(line, "iNum"), out num);
            decimal group;
            bool typed = Dec(Get(line, "iGroupType"), out group);
            if (!hasNum && hasQty && typed && (group == 1m || group == 2m))
            {
                PutMissing(line, fields, "iNum", Num(qty / rate));
            }
            if (hasNum && !hasQty)
            {
                PutMissing(line, fields, "iQuantity", Num(num * rate));
            }
        }

        static bool Positive(string text)
        {
            decimal rate;
            return Dec(text, out rate) && rate > 0m;
        }

        static bool Dec(string text, out decimal value)
        {
            value = 0m;
            if (text == null || text.Length == 0)
            {
                return false;
            }
            return decimal.TryParse(text, NumberStyles.Any, CultureInfo.InvariantCulture, out value);
        }

        static string Num(decimal value)
        {
            decimal rounded = Math.Round(value, 6, MidpointRounding.AwayFromZero);
            return rounded.ToString("0.######", CultureInfo.InvariantCulture);
        }

        static void PutMissing(Dictionary<string, string> line, Dictionary<string, string> fields, string name, string value)
        {
            if (value == null || value.Trim().Length == 0 || Get(line, name).Length > 0)
            {
                return;
            }
            // 模板里没有的列不写：U8 Save 遇到行集外的属性可能整单拒绝。
            string canon;
            if (fields == null || !fields.TryGetValue(name, out canon) || canon == null || canon.Length == 0)
            {
                return;
            }
            line[canon] = value.Trim();
        }

        static string Get(Dictionary<string, string> line, string name)
        {
            if (line == null || name == null)
            {
                return "";
            }
            string value;
            if (!line.TryGetValue(name, out value) || value == null)
            {
                return "";
            }
            return value.Trim();
        }
    }
}
