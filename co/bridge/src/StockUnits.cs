using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 固定换算率存货要带辅计量。08/09 的单价金额 U8 不算，桥按数量和单价补。
    internal static class StockUnits
    {
        const string InvSql = "select iGroupType, cGroupCode, cSTComUnitCode, cComUnitCode from Inventory where cInvCode=?";
        const string RateSql = "select iChangRate from ComputationUnit where cComunitCode=? and cGroupCode=?";
        const string RateOnlySql = "select iChangRate from ComputationUnit where cComunitCode=?";

        public static void FixPrice(List<string> names, Dictionary<string, string> row)
        {
            ApplyPrice(Get(row, "iQuantity"), Get(row, "iUnitCost"), Get(row, "iPrice"),
                delegate(string name, string value) { PutNamed(names, row, name, value); });
        }

        // 改行看的是这次 op 里调用方送来的字段，不看装入行上原来有没有单价。
        public static void FixPriceSent(object dom, object row, bool sentPrice, bool sentCost, bool sentQty, List<string> schema)
        {
            if (sentPrice && !sentCost)
            {
                decimal price;
                decimal qty;
                if (!Dec(DomRows.Get(row, "iPrice"), out price) || !Dec(DomRows.Get(row, "iQuantity"), out qty) || qty == 0m)
                {
                    return;
                }
                StockDom.SetCell(dom, row, "iUnitCost", Price(price / qty), schema);
                return;
            }
            if (!sentQty && !sentCost)
            {
                return;
            }
            decimal cost;
            decimal qtyOnly;
            if (!Dec(DomRows.Get(row, "iUnitCost"), out cost) || !Dec(DomRows.Get(row, "iQuantity"), out qtyOnly))
            {
                return;
            }
            StockDom.SetCell(dom, row, "iPrice", Money(qtyOnly * cost), schema);
        }

        public static void Apply(object conn, List<string> names, Dictionary<string, string> row,
            string qtyName, string numName, bool forceNum)
        {
            UnitAsk ask = Ask(Get(row, "cInvCode"), Get(row, "cAssUnit"), Get(row, "iInvExchRate"),
                Get(row, numName), Get(row, qtyName), forceNum);
            Plan plan = Build(conn, ask);
            if (plan == null)
            {
                return;
            }
            PutNamed(names, row, "cAssUnit", plan.Unit);
            PutNamed(names, row, "iInvExchRate", plan.Rate);
            PutNamed(names, row, numName, plan.Num);
        }

        public static void ApplyDom(object conn, object dom, object row, UnitJob job)
        {
            if (job.Schema == null)
            {
                job.Schema = DomRows.Schema(dom);
            }
            UnitAsk ask = Ask(DomRows.Get(row, "cInvCode"), DomRows.Get(row, "cAssUnit"),
                DomRows.Get(row, "iInvExchRate"), DomRows.Get(row, job.NumName), DomRows.Get(row, job.QtyName), job.Force);
            Plan plan = Build(conn, ask);
            if (plan == null)
            {
                return;
            }
            SetIf(dom, row, "cAssUnit", plan.Unit, job.Schema);
            SetIf(dom, row, "iInvExchRate", plan.Rate, job.Schema);
            SetIf(dom, row, job.NumName, plan.Num, job.Schema);
        }

        public static decimal Round2(decimal value)
        {
            return Math.Round(value, 2, MidpointRounding.AwayFromZero);
        }

        public static decimal Round6(decimal value)
        {
            return Math.Round(value, 6, MidpointRounding.AwayFromZero);
        }

        public static string Money(decimal value)
        {
            return Round2(value).ToString("0.00", CultureInfo.InvariantCulture);
        }

        public static string Price(decimal value)
        {
            return Round6(value).ToString("0.######", CultureInfo.InvariantCulture);
        }

        public static bool Dec(string text, out decimal value)
        {
            value = 0m;
            if (text == null || text.Trim().Length == 0)
            {
                return false;
            }
            return decimal.TryParse(text.Trim(), NumberStyles.Any, CultureInfo.InvariantCulture, out value);
        }

        static void ApplyPrice(string qtyText, string costText, string priceText, Action<string, string> write)
        {
            if (costText.Trim().Length > 0)
            {
                decimal qty;
                decimal cost;
                if (Dec(qtyText, out qty) && Dec(costText, out cost))
                {
                    write("iPrice", Money(qty * cost));
                }
                return;
            }
            decimal price;
            decimal qtyOnly;
            if (priceText.Trim().Length == 0 || !Dec(priceText, out price) || !Dec(qtyText, out qtyOnly) || qtyOnly == 0m)
            {
                return;
            }
            write("iUnitCost", Price(price / qtyOnly));
        }

        static Plan Build(object conn, UnitAsk ask)
        {
            string code = Trimmed(ask.Inv);
            if (code.Length == 0)
            {
                return null;
            }
            Dictionary<string, object> row = Rows.One(conn, InvSql, new object[] { code });
            if (row == null || !Grouped(CoRows.Col(row, "iGroupType")))
            {
                return null;
            }
            Plan plan = new Plan();
            plan.Unit = PickUnit(ask.Unit, row);
            plan.Rate = PickRate(conn, ask.Rate, plan.Unit, row);
            plan.Num = PickNum(ask, plan.Rate);
            if (EmptyPlan(plan))
            {
                return null;
            }
            return plan;
        }

        static UnitAsk Ask(string inv, string unit, string rate, string num, string qty, bool forceNum)
        {
            UnitAsk ask = new UnitAsk();
            ask.Inv = inv;
            ask.Unit = unit;
            ask.Rate = rate;
            ask.Num = num;
            ask.Qty = qty;
            ask.ForceNum = forceNum;
            return ask;
        }

        static string Trimmed(string text)
        {
            return text == null ? "" : text.Trim();
        }

        static string PickUnit(string unit, Dictionary<string, object> row)
        {
            string picked = Trimmed(unit);
            if (picked.Length > 0)
            {
                return picked;
            }
            picked = CoRows.Col(row, "cSTComUnitCode");
            if (picked.Length > 0)
            {
                return picked;
            }
            return CoRows.Col(row, "cComUnitCode");
        }

        static string PickRate(object conn, string rateText, string unit, Dictionary<string, object> row)
        {
            string rate = Trimmed(rateText);
            decimal known;
            if (Dec(rate, out known) && known > 0m)
            {
                return rate;
            }
            return RateOf(conn, unit, CoRows.Col(row, "cComUnitCode"), CoRows.Col(row, "cGroupCode"));
        }

        static string PickNum(UnitAsk ask, string rate)
        {
            string num = Trimmed(ask.Num);
            if ((ask.ForceNum || num.Length == 0) && rate.Length > 0)
            {
                return NumOf(ask.Qty, rate);
            }
            return num;
        }

        static bool EmptyPlan(Plan plan)
        {
            return plan.Unit.Length == 0 && plan.Rate.Length == 0 && plan.Num.Length == 0;
        }

        static string RateOf(object conn, string unit, string main, string group)
        {
            if (unit.Length == 0)
            {
                return "";
            }
            if (main.Length > 0 && string.Equals(unit, main.Trim(), StringComparison.OrdinalIgnoreCase))
            {
                return "1";
            }
            Dictionary<string, object> row = null;
            if (group.Trim().Length > 0)
            {
                row = Rows.One(conn, RateSql, new object[] { unit, group.Trim() });
            }
            if (row == null)
            {
                row = Rows.One(conn, RateOnlySql, new object[] { unit });
            }
            if (row == null)
            {
                return "";
            }
            decimal rate;
            string text = CoRows.Col(row, "iChangRate");
            if (!Dec(text, out rate) || rate <= 0m)
            {
                return "";
            }
            return text.Trim();
        }

        static string NumOf(string qtyText, string rateText)
        {
            decimal qty;
            decimal rate;
            if (!Dec(qtyText, out qty) || !Dec(rateText, out rate) || rate <= 0m)
            {
                return "";
            }
            return Price(qty / rate);
        }

        static void SetIf(object dom, object row, string name, string value, List<string> schema)
        {
            if (value == null || value.Length == 0)
            {
                return;
            }
            StockDom.SetCell(dom, row, name, value, schema);
        }

        static bool Grouped(string text)
        {
            decimal group;
            return Dec(text, out group) && group != 0m;
        }

        static void PutNamed(List<string> names, Dictionary<string, string> row, string key, string value)
        {
            if (value == null || value.Length == 0)
            {
                return;
            }
            string canon = Canon(names, key);
            if (canon == null)
            {
                return;
            }
            row[canon] = value;
        }

        static string Canon(List<string> names, string key)
        {
            if (names == null)
            {
                return key;
            }
            for (int i = 0; i < names.Count; i++)
            {
                if (string.Equals(names[i], key, StringComparison.OrdinalIgnoreCase))
                {
                    return names[i];
                }
            }
            return null;
        }

        static string Get(Dictionary<string, string> row, string name)
        {
            if (row == null || name == null)
            {
                return "";
            }
            string value;
            if (!row.TryGetValue(name, out value) || value == null)
            {
                return "";
            }
            return value.Trim();
        }

        sealed class Plan
        {
            public string Unit;
            public string Rate;
            public string Num;
        }

        sealed class UnitAsk
        {
            public string Inv;
            public string Unit;
            public string Rate;
            public string Num;
            public string Qty;
            public bool ForceNum;
        }

        internal sealed class UnitJob
        {
            public string QtyName;
            public string NumName;
            public bool Force;
            public List<string> Schema;
        }
    }
}
