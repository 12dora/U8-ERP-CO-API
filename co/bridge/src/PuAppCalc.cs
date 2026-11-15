using System;
using System.Collections.Generic;

namespace U8Co
{
    // 请购单表体的单位与金额。CO 的 VoucherSave2 不算价，由桥填（同 PuCalc 的取整规则）。
    // 币种固定本位币（WorkContext.HomeCurrency，一个请求只查一次）、汇率 1，本币列等于原币列。
    internal static class PuAppCalc
    {
        // 金额列的含义取自 voucheritems_lang（卡片 27）：ioricost 原币单价、ioritaxcost 原币含税单价、iorimoney 原币金额、
        // ioritaxprice 原币税额、iorisum 原币价税合计、funitprice / ftaxprice 本币单价 / 含税单价、imoney 本币金额、
        // itaxprice 本币税额、fmoney 本币价税合计。保存后 U8 界面显示是否一致，未经实测。
        static readonly string[][] Money = new string[][]
        {
            new string[] { "unit", "ioricost", "funitprice" },
            new string[] { "taxprice", "ioritaxcost", "ftaxprice" },
            new string[] { "money", "iorimoney", "imoney" },
            new string[] { "tax", "ioritaxprice", "itaxprice" },
            new string[] { "sum", "iorisum", "fmoney" }
        };
        static readonly string[] Keep = new string[]
        {
            "cinvcode", "fquantity", "ioricost", "ioritaxcost", "ipertaxrate", "btaxcost", "cunitid", "iinvexchrate", "fnum"
        };

        // 行上现有的计算用字段（修改时打底）；新增行 row 为 null。
        public static Dictionary<string, string> FromRow(object row)
        {
            Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            if (row == null)
            {
                return map;
            }
            for (int i = 0; i < Keep.Length; i++)
            {
                string text = DomRows.Get(row, Keep[i]).Trim();
                if (text.Length > 0)
                {
                    map[Keep[i]] = text;
                }
            }
            return map;
        }

        // 新增行、换存货或改数量时重填单位；caller 是调用方这次送的字段。返回要写回行上的单位字段（空值表示清掉）。
        public static Dictionary<string, string> Units(object conn, Dictionary<string, string> calc, Dictionary<string, string> caller, bool adding)
        {
            Dictionary<string, string> put = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            bool invChanged = caller.ContainsKey("cinvcode") && !Same(caller["cinvcode"], PuAppFields.Get(calc, "cinvcode"));
            if (!adding && !invChanged && !caller.ContainsKey("fquantity"))
            {
                return put;
            }
            Dictionary<string, string> po = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            po["cinvcode"] = caller.ContainsKey("cinvcode") ? caller["cinvcode"] : PuAppFields.Get(calc, "cinvcode");
            po["iquantity"] = caller.ContainsKey("fquantity") ? caller["fquantity"] : PuAppFields.Get(calc, "fquantity");
            if (!adding && !invChanged && PuAppFields.Has(calc, "cunitid"))
            {
                po["cunitid"] = calc["cunitid"];
            }
            PuFields.ApplyUnit(conn, po, new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase));
            put["cunitid"] = PuAppFields.Get(po, "cunitid");
            put["iinvexchrate"] = PuAppFields.Get(po, "iinvexchrate");
            put["fnum"] = PuAppFields.Get(po, "inum");
            return put;
        }

        // 有单价时算金额。calc 已叠上调用方字段。没有单价返回空表（不写金额）。
        public static Dictionary<string, string> Prices(WorkContext ctx, Dictionary<string, string> calc, Dictionary<string, string> caller, bool adding)
        {
            Dictionary<string, string> put = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            if (!adding && !Touched(caller))
            {
                return put;
            }
            bool tax = TaxMode(calc, caller);
            string priceText = PuAppFields.Get(calc, tax ? "ioritaxcost" : "ioricost");
            if (priceText.Trim().Length == 0)
            {
                return put;
            }
            decimal qty = PuCalc.Need(PuAppFields.Get(calc, "fquantity"), "数量不正确");
            decimal rate = RateOf(ctx.Conn, calc);
            decimal price = PuCalc.Need(priceText, "单价不正确");
            Dictionary<string, decimal> v = tax ? FromTax(qty, price, rate / 100m) : FromNet(qty, price, rate / 100m);
            for (int i = 0; i < Money.Length; i++)
            {
                string text = PuCalc.Text(v[Money[i][0]]);
                put[Money[i][1]] = text;
                put[Money[i][2]] = text;
            }
            put["ipertaxrate"] = PuCalc.Text(rate);
            put["btaxcost"] = tax ? "1" : "0";
            put["cexch_name"] = ctx.HomeCurrency;
            put["iexchrate"] = "1";
            return put;
        }

        // 修改的行必须是本位币、汇率 1：桥只按本币等于原币算价，外币行改了会被悄悄写成本位币。
        public static void RequireLocal(WorkContext ctx, object row)
        {
            string name = DomRows.Get(row, "cexch_name").Trim();
            decimal rate;
            bool rateOk = !PuCalc.TryDec(DomRows.Get(row, "iexchrate"), out rate) || rate == 1m;
            if ((name.Length > 0 && name != ctx.HomeCurrency) || !rateOk)
            {
                throw new BridgeException(409, "state_mismatch", "外币请购单行本期不支持修改，请在 U8 客户端处理");
            }
        }

        // 新增行的币种和汇率（卡片 27 必输），不论有没有单价。
        public static void Currencies(WorkContext ctx, Dictionary<string, string> put)
        {
            if (!put.ContainsKey("cexch_name"))
            {
                put["cexch_name"] = ctx.HomeCurrency;
            }
            if (!put.ContainsKey("iexchrate"))
            {
                put["iexchrate"] = "1";
            }
        }

        static bool Touched(Dictionary<string, string> caller)
        {
            return caller.ContainsKey("fquantity") || caller.ContainsKey("ioricost")
                || caller.ContainsKey("ioritaxcost") || caller.ContainsKey("ipertaxrate");
        }

        static bool TaxMode(Dictionary<string, string> calc, Dictionary<string, string> caller)
        {
            if (caller.ContainsKey("ioritaxcost"))
            {
                return true;
            }
            if (caller.ContainsKey("ioricost"))
            {
                return false;
            }
            return PuCalc.Tax(PuAppFields.Get(calc, "btaxcost"));
        }

        static decimal RateOf(object conn, Dictionary<string, string> calc)
        {
            decimal rate;
            if (PuCalc.TryDec(PuAppFields.Get(calc, "ipertaxrate"), out rate))
            {
                return rate;
            }
            string inv = PuAppFields.Get(calc, "cinvcode").Trim();
            if (inv.Length > 0 && PuCalc.TryDec(Rows.Scalar(conn, PuCalc.InvTaxSql, new object[] { inv }), out rate) && rate >= 0m && rate < 1000m)
            {
                return rate;
            }
            return 13m;
        }

        static Dictionary<string, decimal> FromNet(decimal qty, decimal unit, decimal r)
        {
            try
            {
                unit = Round(unit, 6);
                decimal money = Round(qty * unit, 2);
                decimal tax = Round(money * r, 2);
                return Pack(unit, Round(unit * (1m + r), 6), money, tax, money + tax);
            }
            catch (OverflowException)
            {
                throw new BridgeException(400, "bad_request", "数量或单价超出范围");
            }
        }

        static Dictionary<string, decimal> FromTax(decimal qty, decimal taxPrice, decimal r)
        {
            try
            {
                taxPrice = Round(taxPrice, 6);
                decimal sum = Round(qty * taxPrice, 2);
                decimal money = Round(sum / (1m + r), 2);
                return Pack(Round(taxPrice / (1m + r), 6), taxPrice, money, sum - money, sum);
            }
            catch (OverflowException)
            {
                throw new BridgeException(400, "bad_request", "数量或单价超出范围");
            }
        }

        static Dictionary<string, decimal> Pack(decimal unit, decimal taxPrice, decimal money, decimal tax, decimal sum)
        {
            Dictionary<string, decimal> v = new Dictionary<string, decimal>();
            v["unit"] = unit;
            v["taxprice"] = taxPrice;
            v["money"] = money;
            v["tax"] = tax;
            v["sum"] = sum;
            return v;
        }

        static decimal Round(decimal value, int digits)
        {
            return Math.Round(value, digits, MidpointRounding.AwayFromZero);
        }

        static bool Same(string a, string b)
        {
            return string.Equals((a ?? "").Trim(), (b ?? "").Trim(), StringComparison.OrdinalIgnoreCase);
        }
    }
}
