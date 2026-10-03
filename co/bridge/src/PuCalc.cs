using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 采购订单金额。PU 的 BodyCheck 不算价。单价 6 位、金额 2 位，四舍五入远离 0。
    // 含税：10000 × 1.13、税率 13 → 价税合计 11300、无税 10000.00、税额 1300.00、无税单价 1.000000。
    internal static class PuCalc
    {
        // 存货档案税率，采购订单（PurchaseEdit）和请购单（PuAppCalc）新行缺省税率共用。
        internal const string InvTaxSql = "select iTaxRate from Inventory where cInvCode=?";

        public static void Apply(Dictionary<string, string> line, decimal exch)
        {
            if (line == null)
            {
                throw new BridgeException(500, "internal", "缺少表体行");
            }
            if (exch <= 0m)
            {
                throw new BridgeException(400, "bad_request", "汇率必须大于 0");
            }
            decimal qty = Need(Get(line, "iquantity"), "数量不正确");
            if (qty <= 0m)
            {
                throw new BridgeException(400, "bad_request", "数量必须大于 0");
            }
            decimal rate = Need(Get(line, "ipertaxrate"), "税率不正确");
            if (rate < 0m || rate >= 1000m)
            {
                throw new BridgeException(400, "bad_request", "税率不正确");
            }
            decimal r = rate / 100m;
            if (1m + r == 0m)
            {
                throw new BridgeException(400, "bad_request", "税率不正确");
            }
            bool tax = Tax(Get(line, "btaxcost"));
            try
            {
                if (tax)
                {
                    FillTax(line, qty, Need(Get(line, "itaxprice"), "单价不正确"), r, exch);
                }
                else
                {
                    FillNet(line, qty, Need(Get(line, "iunitprice"), "单价不正确"), r, exch);
                }
            }
            catch (OverflowException)
            {
                throw new BridgeException(400, "bad_request", "数量或单价超出范围");
            }
        }

        static void FillTax(Dictionary<string, string> line, decimal qty, decimal taxPrice, decimal r, decimal exch)
        {
            if (taxPrice < 0m)
            {
                throw new BridgeException(400, "bad_request", "单价不正确");
            }
            taxPrice = Round6(taxPrice);
            decimal sum = Round2(qty * taxPrice);
            decimal money = Round2(sum / (1m + r));
            decimal tax = sum - money;
            decimal unit = Round6(taxPrice / (1m + r));
            Put(line, "btaxcost", "1");
            Put(line, "itaxprice", Text(taxPrice));
            Put(line, "iunitprice", Text(unit));
            Put(line, "isum", Text(sum));
            Put(line, "imoney", Text(money));
            Put(line, "itax", Text(tax));
            Native(line, unit, money, tax, sum, exch);
        }

        static void FillNet(Dictionary<string, string> line, decimal qty, decimal unit, decimal r, decimal exch)
        {
            if (unit < 0m)
            {
                throw new BridgeException(400, "bad_request", "单价不正确");
            }
            unit = Round6(unit);
            decimal money = Round2(qty * unit);
            decimal tax = Round2(money * r);
            decimal sum = money + tax;
            decimal taxPrice = Round6(unit * (1m + r));
            Put(line, "btaxcost", "0");
            Put(line, "iunitprice", Text(unit));
            Put(line, "itaxprice", Text(taxPrice));
            Put(line, "imoney", Text(money));
            Put(line, "itax", Text(tax));
            Put(line, "isum", Text(sum));
            Native(line, unit, money, tax, sum, exch);
        }

        static void Native(Dictionary<string, string> line, decimal unit, decimal money, decimal tax, decimal sum, decimal exch)
        {
            Put(line, "inatunitprice", Text(Round6(unit * exch)));
            Put(line, "inatmoney", Text(Round2(money * exch)));
            Put(line, "inattax", Text(Round2(tax * exch)));
            Put(line, "inatsum", Text(Round2(sum * exch)));
        }

        public static bool Tax(string text)
        {
            if (text == null)
            {
                return false;
            }
            string value = text.Trim();
            if (value.Length == 0 || value == "0" || value.Equals("false", StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            if (value == "1" || value == "-1" || value.Equals("true", StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
            decimal number;
            if (!TryDec(value, out number))
            {
                return false;
            }
            return number != 0m;
        }

        public static decimal Need(string text, string message)
        {
            decimal number;
            if (!TryDec(text, out number))
            {
                throw new BridgeException(400, "bad_request", message);
            }
            return number;
        }

        public static bool TryDec(string text, out decimal value)
        {
            value = 0m;
            if (text == null)
            {
                return false;
            }
            string raw = text.Trim();
            if (raw.Length == 0)
            {
                return false;
            }
            return decimal.TryParse(raw, NumberStyles.Number, CultureInfo.InvariantCulture, out value);
        }

        public static string Text(decimal value)
        {
            return value.ToString("0.#############################", CultureInfo.InvariantCulture);
        }

        public static string Qty6(decimal value)
        {
            return Text(Round6(value));
        }

        static decimal Round2(decimal value)
        {
            return Math.Round(value, 2, MidpointRounding.AwayFromZero);
        }

        static decimal Round6(decimal value)
        {
            return Math.Round(value, 6, MidpointRounding.AwayFromZero);
        }

        static void Put(Dictionary<string, string> line, string name, string value)
        {
            line[name] = value;
        }

        static string Get(Dictionary<string, string> line, string name)
        {
            string text;
            if (line.TryGetValue(name, out text) && text != null)
            {
                return text;
            }
            return "";
        }
    }
}
