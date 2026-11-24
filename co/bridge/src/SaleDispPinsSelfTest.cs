using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的发货单参照销售订单表体扩展自定义项 cbdefineN（DispatchLists_extradefine）：收集（按来源订单行 iSOsID 记下）、
    // 不给表体扩展自定义项时不核对行（InvLines 为空）、不进 DOM、保存后发货单行核对（SalePins.MatchDispatch）、补写 SQL 形状。不连库。
    internal static class SaleDispPinsSelfTest
    {
        public static void Run()
        {
            CheckCollect();
            CheckNoLinePins();
            CheckDomOnly();
            CheckSql();
            CheckMatch();
            CheckRange();
            CheckRangeFixed();
            CheckRangePin();
            CheckCanonical();
            CheckCanonicalRefused();
            CheckDropSql();
        }

        // 删除时清理扩展表（SaleExtraDrop）：先按表体外键取行主键，再按表头主键 / 行主键删；表名列名只来自 SaleExtraRead.Tables。
        static void CheckDropSql()
        {
            string[] disp = SaleExtraRead.FindName("dispatch");
            Expect("drop disp lines", SaleExtraDrop.LinesSql(disp) == "SELECT [iDLsID] FROM [DispatchLists] WHERE [DLID]=?");
            Expect("drop disp head", SaleExtraDrop.HeadSql(disp) == "DELETE FROM [DispatchList_extradefine] WHERE [DLID]=?");
            Expect("drop disp line", SaleExtraDrop.LineSql(disp) == "DELETE FROM [DispatchLists_extradefine] WHERE [iDLsID]=?");
            string[] inv = SaleExtraRead.FindName("sale_invoice");
            Expect("drop inv lines", SaleExtraDrop.LinesSql(inv) == "SELECT [AutoID] FROM [SaleBillVouchs] WHERE [SBVID]=?");
            Expect("drop inv line", SaleExtraDrop.LineSql(inv) == "DELETE FROM [SaleBillVouchs_extradefine] WHERE [AutoID]=?");
            Expect("drop other kind", SaleExtraDrop.Capture(null, "sale_return", 5) == null);
            Expect("drop no id", SaleExtraDrop.Capture(null, "dispatch", 0) == null);
        }

        // 桥写的数值按列类型改写成纯文本（SalePinsRange.Canonical）：nvarchar 参数 '3.0' → int、'1E+01' → decimal 会在 Save 之后报 SQL 错误。
        static void CheckCanonical()
        {
            string[] ints = new string[] { "3.0", "3.000", "3", " 3 ", "+3" };
            for (int i = 0; i < ints.Length; i++)
            {
                Expect("canon int " + ints[i], Normalized("int", 10, 0, ints[i]) == "3");
            }
            Expect("canon int exponent", Normalized("int", 10, 0, "1E+01") == "10");
            Expect("canon tinyint exponent", Normalized("tinyint", 3, 0, "2.5E+1") == "25");
            Expect("canon dec exponent", Normalized("decimal", 18, 2, "1E+01") == "10");
            Expect("canon dec zeros", Normalized("decimal", 18, 4, "3.000") == "3");
            Expect("canon dec neg", Normalized("numeric", 18, 4, "-0.50") == "-0.5");
            Expect("canon money", Normalized("money", 19, 4, "1.2340E+2") == "123.4");
            Expect("canon float", Normalized("float", 53, 0, "1.5E-3") == "0.0015");
            Expect("canon nvarchar kept", Normalized("nvarchar", 0, 0, "1E+01") == "1E+01");
            SalePin calc = RangePin("int", 10, 0, "1E+01");
            calc.Calc = true;
            SalePins.CheckValue(calc);
            Expect("canon calc untouched", calc.Value == "1E+01");
        }

        // 非 Calc 数值只收十进制写法：千分位、括号负数、货币符号 400（field 为字段路径）。
        static void CheckCanonicalRefused()
        {
            string[] bad = new string[] { "1,000", "(3)", "$3", "3-", "0x10" };
            for (int i = 0; i < bad.Length; i++)
            {
                bool refused = false;
                try
                {
                    SalePins.CheckValue(RangePin("decimal", 18, 2, bad[i]));
                }
                catch (BridgeException ex)
                {
                    refused = ex.Status == 400 && ex.Field == "lines.0.cbdefine6";
                }
                Expect("canon refused " + bad[i], refused);
            }
        }

        static string Normalized(string type, int prec, int scale, string value)
        {
            SalePin pin = RangePin(type, prec, scale, value);
            SalePins.CheckValue(pin);
            return pin.Value;
        }

        static SalePin RangePin(string type, int prec, int scale, string value)
        {
            SalePin pin = new SalePin();
            pin.Path = "lines.0.cbdefine6";
            pin.Field = "cbdefine6";
            pin.Value = value;
            pin.Col = Col(type, prec, scale);
            if (pin.Col.Kind == "text")
            {
                pin.Col.Len = 20;
            }
            return pin;
        }

        // CheckValue 带上范围：表示不了的值 400，field 是字段路径；只走组件的 Calc 字段不收紧。
        static void CheckRangePin()
        {
            SalePin pin = new SalePin();
            pin.Path = "lines.1.cbdefine6";
            pin.Field = "cbdefine6";
            pin.Value = "256";
            pin.Col = Col("tinyint", 3, 0);
            bool refused = false;
            try
            {
                SalePins.CheckValue(pin);
            }
            catch (BridgeException ex)
            {
                refused = ex.Status == 400 && ex.Field == "lines.1.cbdefine6" && ex.Message.Contains("tinyint");
            }
            Expect("range pin refused", refused);
            pin.Value = "255";
            SalePins.CheckValue(pin);
            pin.Value = "256";
            pin.Calc = true;
            SalePins.CheckValue(pin);
        }

        // 数值列表示不了的值保存前 400（SalePinsRange）：整数类型的范围和小数、decimal(p,s) 的精度和小数位、money 的 4 位小数。
        static void CheckRange()
        {
            Expect("range tinyint ok", SalePins.NumberProblem(Col("tinyint", 3, 0), 255m) == "");
            Expect("range tinyint over", SalePins.NumberProblem(Col("tinyint", 3, 0), 256m).Length > 0);
            Expect("range tinyint neg", SalePins.NumberProblem(Col("tinyint", 3, 0), -1m).Length > 0);
            Expect("range smallint frac", SalePins.NumberProblem(Col("smallint", 5, 0), 1.5m).Length > 0);
            Expect("range int trailing zeros", SalePins.NumberProblem(Col("int", 10, 0), 3.000m) == "");
            Expect("range int over", SalePins.NumberProblem(Col("int", 10, 0), 2147483648m).Length > 0);
            Expect("range bigint ok", SalePins.NumberProblem(Col("bigint", 19, 0), -9223372036854775808m) == "");
            Expect("range money 4dp", SalePins.NumberProblem(Col("money", 19, 4), 1.2345m) == ""
                && SalePins.NumberProblem(Col("money", 19, 4), 1.23456m).Length > 0);
            Expect("range smallmoney over", SalePins.NumberProblem(Col("smallmoney", 10, 4), 214748.3648m).Length > 0);
            Expect("range float free", SalePins.NumberProblem(Col("float", 53, 0), 12345678901234567890.123456m) == "");
            Expect("range null col", SalePins.NumberProblem(null, 1m) == "");
        }

        static void CheckRangeFixed()
        {
            SaleCol dec = Col("decimal", 5, 2);
            Expect("range dec ok", SalePins.NumberProblem(dec, 999.99m) == "" && SalePins.NumberProblem(dec, -999.99m) == "");
            Expect("range dec scale", SalePins.NumberProblem(dec, 1.234m).Length > 0);
            Expect("range dec scale zeros", SalePins.NumberProblem(dec, 1.2300m) == "");
            Expect("range dec prec", SalePins.NumberProblem(dec, 1000m).Length > 0);
            Expect("range numeric no scale", SalePins.NumberProblem(Col("numeric", 3, 0), 0.5m).Length > 0);
            Expect("range dec unknown prec", SalePins.NumberProblem(Col("decimal", 0, 0), 1.23456m) == "");
            Expect("decimals", SalePins.Decimals(12.345600m) == 4 && SalePins.Decimals(10m) == 0 && SalePins.Decimals(-0.5m) == 1);
            Expect("int digits", SalePins.IntDigits(0.5m) == 0 && SalePins.IntDigits(-123.4m) == 3);
        }

        static SaleCol Col(string type, int prec, int scale)
        {
            SaleCol col = new SaleCol();
            col.Type = type;
            col.Kind = SalePins.KindOf(type);
            col.Prec = prec;
            col.Scale = scale;
            return col;
        }

        static List<InvPinLine> Lines()
        {
            List<InvPinLine> lines = new List<InvPinLine>();
            lines.Add(PinLine(31, 2m, Map("source_line_id", 31, "quantity", 2, "cbdefine5", "文本A", "cWhCode", "01")));
            lines.Add(PinLine(32, 1m, Map("source_line_id", 32, "quantity", 1, "cbdefine07", "文本B", "cmemo", "m")));
            lines.Add(PinLine(33, 3m, Map("source_line_id", 33, "quantity", 3, "CBDEFINE6", 1.5m, "cbdefine7", "2026-01-15",
                "cbdefine8", "")));
            return lines;
        }

        static void CheckCollect()
        {
            SalePins pins = SalePins.Collect(SalePins.Dispatch, Map("caddcode", "A01"), null, false);
            SalePins.TakeDispatchLines(pins, Lines());
            Expect("disp pins count", pins.Items.Count == 4 && pins.InvLines.Count == 3);
            CheckCollectItems(pins);
        }

        static void CheckCollectItems(SalePins pins)
        {
            SalePin head = pins.Items[0];
            Expect("disp head pin", head.Path == "head.caddcode" && head.SrcLine == 0);
            SalePin first = pins.Items[1];
            Expect("disp line first", first.Path == "lines.0.cbdefine5" && first.SrcLine == 31 && first.Value == "文本A");
            Expect("disp line table", first.Table == "DispatchLists_extradefine" && first.KeyColumn == "iDLsID" && first.Explicit);
            SalePin six = pins.Items[2];
            Expect("disp line six", six.Path == "lines.2.cbdefine6" && six.SrcLine == 33 && six.Value == "1.5");
            SalePin seven = pins.Items[3];
            Expect("disp line seven", seven.Path == "lines.2.cbdefine7" && seven.SrcLine == 33 && seven.Column == "cbdefine7");
        }

        // 没有表体扩展自定义项：不保留 InvLines，保存后不核对行（与本改动之前一样）。
        static void CheckNoLinePins()
        {
            List<InvPinLine> lines = new List<InvPinLine>();
            lines.Add(PinLine(31, 2m, Map("source_line_id", 31, "quantity", 2, "cwhcode", "01", "cbdefine5", "")));
            SalePins pins = SalePins.Collect(SalePins.Dispatch, Map("caddcode", "A01"), null, false);
            SalePins.TakeDispatchLines(pins, lines);
            Expect("disp no line pins", pins.Items.Count == 1 && pins.InvLines.Count == 0);
            SalePins none = SalePins.Collect(SalePins.Dispatch, null, null, false);
            SalePins.TakeDispatchLines(none, null);
            Expect("disp null lines", none.Items.Count == 0 && none.InvLines.Count == 0);
        }

        static void CheckDomOnly()
        {
            Dictionary<string, object> fields = Lines()[0].Fields;
            Dictionary<string, object> dom = SalePins.DomOnly(SalePins.Dispatch, fields);
            Expect("disp dom only", dom.Count == 3 && dom.ContainsKey("cWhCode") && !dom.ContainsKey("cbdefine5")
                && fields.ContainsKey("cbdefine5"));
            Expect("disp line off dom", SalePins.OffDom(SalePins.Dispatch, "cbdefine5", false, true));
        }

        static void CheckSql()
        {
            SalePins pins = SalePins.Collect(SalePins.Dispatch, null, null, false);
            SalePins.TakeDispatchLines(pins, Lines());
            Expect("disp write sql", SalePins.WriteSql(pins.Items[0]).StartsWith(
                "IF EXISTS (SELECT 1 FROM [DispatchLists_extradefine] WHERE [iDLsID]=?) UPDATE [DispatchLists_extradefine] SET [cbdefine5]=?",
                StringComparison.Ordinal));
            Expect("disp read sql", SalePins.ReadSql(pins.Items[0])
                == "SELECT [cbdefine5] AS v FROM [DispatchLists_extradefine] WHERE [iDLsID]=?");
        }

        static void CheckMatch()
        {
            List<InvPinLine> want = new List<InvPinLine>();
            want.Add(PinLine(31, 2m, null));
            want.Add(PinLine(32, 0.5m, null));
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            rows.Add(Map("iDLsID", "7001", "iSOsID", "31", "q", "2.000000"));
            rows.Add(Map("iDLsID", "7002", "iSOsID", "32", "q", "0.500000"));
            Dictionary<int, int> map = new Dictionary<int, int>();
            Expect("disp match ok", SalePins.MatchDispatch(rows, want, map) == "" && map[31] == 7001 && map[32] == 7002);
            rows[1]["q"] = "1.000000";
            Expect("disp match qty", SalePins.MatchDispatch(rows, want, new Dictionary<int, int>()).Length > 0);
            rows[1]["q"] = "0.500000";
            rows.Add(Map("iDLsID", "7003", "iSOsID", "33", "q", "1"));
            Expect("disp match extra row", SalePins.MatchDispatch(rows, want, new Dictionary<int, int>()).Length > 0);
            rows.RemoveAt(2);
            rows[1]["iSOsID"] = "31";
            Expect("disp match dup src", SalePins.MatchDispatch(rows, want, new Dictionary<int, int>()).Length > 0);
            rows[1]["iSOsID"] = "";
            Expect("disp match no src", SalePins.MatchDispatch(rows, want, new Dictionary<int, int>()).Length > 0);
            // 数量读回已是 decimal(38,6) 文本；6 位小数的请求与之相等，第 7 位起按 AwayFromZero 规整后比较。
            List<InvPinLine> fine = new List<InvPinLine>();
            fine.Add(PinLine(31, 12.345678m, null));
            List<Dictionary<string, object>> fineRows = new List<Dictionary<string, object>>();
            fineRows.Add(Map("iDLsID", "7001", "iSOsID", "31", "q", "12.345678"));
            Expect("disp match 6dp", SalePins.MatchDispatch(fineRows, fine, new Dictionary<int, int>()) == "");
            fineRows[0]["q"] = "12.345700";
            Expect("disp match 6dp differs", SalePins.MatchDispatch(fineRows, fine, new Dictionary<int, int>()).Length > 0);
        }

        static InvPinLine PinLine(int src, decimal qty, Dictionary<string, object> fields)
        {
            InvPinLine line = new InvPinLine();
            line.SrcLine = src;
            line.Qty = qty;
            line.Fields = fields ?? new Dictionary<string, object>();
            return line;
        }

        static Dictionary<string, object> Map(params object[] pairs)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                map[(string)pairs[i]] = pairs[i + 1];
            }
            return map;
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("sale_disp_pins " + name);
            }
        }
    }
}
