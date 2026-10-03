using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // --selftest 的制单现金流量项目（cash_items，ArapCashItems）：请求校验（两条路由都收、对象、个数、键值形状）、
    // 给了用给的、没给按数据来源推、推不出 409 提示 cash_items、没有现金流量行的科目 400；另有坏账收回用过的收款单拒绝制单的文案。不连库。
    internal static class ArapCashItemsSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckBadInput();
            CheckPick();
            CheckFinish();
            CheckBadRecovered();
        }

        static void CheckParse()
        {
            VoucherAsk one = ArapVoucherReq.Parse(Body("{\"flag\":\"AR\",\"type\":\"ar_receipt\",\"id\":5,\"cash_items\":{\" 660399 \":\" 07 \"}}"));
            Expect("ci voucher", one.CashItems.Items.Count == 1 && one.CashItems.Items["660399"] == "07");
            VoucherAsk none = ArapVoucherReq.Parse(Body("{\"flag\":\"AR\",\"type\":\"ar_receipt\",\"id\":5,\"cash_items\":null}"));
            Expect("ci null", none.CashItems != null && none.CashItems.Items.Count == 0);
            ProcVoucherAsk proc = ArapProcVoucherReq.Parse(Body("{\"flag\":\"AR\",\"cancel_nos\":[\"PJTAR000000000001\"],\"cash_items\":{\"660399\":\"07\"}}"));
            Expect("ci proc", proc.CashItems.Items.Count == 1 && proc.CashItems.Items.ContainsKey("660399"));
            Expect("ci proc absent", ArapProcVoucherReq.Parse(Body("{\"flag\":\"AR\",\"cancel_nos\":[\"BZAR1\"]}")).CashItems.Items.Count == 0);
            Expect("ci spec", Array.IndexOf(ArapProcVoucherReq.Spec, "cash_items") > 0
                && Array.IndexOf(ArapVoucherReq.Fields(false), "cash_items") >= 0 && Array.IndexOf(ArapVoucherReq.Fields(true), "cash_items") < 0);
        }

        static void CheckBadInput()
        {
            Bad("ci list", "[\"660399\"]", "cash_items");
            Bad("ci text", "\"660399\"", "cash_items");
            Bad("ci blank key", "{\" \":\"07\"}", "cash_items. ");
            Bad("ci space key", "{\"6603 99\":\"07\"}", "cash_items.6603 99");
            Bad("ci long key", "{\"" + new string('1', ArapCashItems.AccountMax + 1) + "\":\"07\"}",
                "cash_items." + new string('1', ArapCashItems.AccountMax + 1));
            Bad("ci num value", "{\"660399\":7}", "cash_items.660399");
            Bad("ci empty value", "{\"660399\":\"\"}", "cash_items.660399");
            Bad("ci long value", "{\"660399\":\"" + new string('0', ArapCashItems.ItemMax + 1) + "\"}", "cash_items.660399");
            Bad("ci dup", "{\"6604a\":\"07\",\"6604A\":\"08\"}", "cash_items.6604A");
            StringBuilder many = new StringBuilder("{");
            for (int i = 0; i <= ArapCashItems.Max; i++)
            {
                many.Append(i == 0 ? "" : ",").Append("\"6603").Append(i.ToString("00", CultureInfo.InvariantCulture)).Append("\":\"07\"");
            }
            Bad("ci too many", many.Append("}").ToString(), "cash_items");
            Expect("ci empty object", ArapCashItems.Parse(new Dictionary<string, object>()).Items.Count == 0);
        }

        // 给了的科目用给的（不论数据来源能否推出），记入 Used；没给的照推；推不出 409，文案提示 cash_items。
        static void CheckPick()
        {
            List<Dictionary<string, object>> src = new List<Dictionary<string, object>>();
            src.Add(Source("07", "66029901", "0"));
            src.Add(Source("01", "1122", "0"));
            CashItemMap given = Map("660399", "08");
            given.Items["66029901"] = "09";
            Expect("ci pick given", ArapCashItems.Pick(src, Gl("660399", 1m, 0m), given) == "08" && given.Used.Contains("660399"));
            Expect("ci pick override", ArapCashItems.Pick(src, Gl("66029901", 0m, -5m), given) == "09");
            Expect("ci pick derive", ArapCashItems.Pick(src, Gl("112201", 0m, 5m), given) == "01" && given.Used.Count == 2);
            Expect("ci pick null map", ArapCashItems.Pick(src, Gl("66029901", 0m, 3m), null) == "07");
            try
            {
                ArapCashItems.Pick(src, Gl("660405", 1m, 0m), given);
                throw new InvalidOperationException("ci pick none");
            }
            catch (BridgeException ex)
            {
                Expect("ci pick none", ex.Status == 409 && ex.Message.IndexOf("cash_items", StringComparison.Ordinal) > 0
                    && ex.Message.IndexOf("660405", StringComparison.Ordinal) > 0);
            }
        }

        // 没用到的科目在查项目之前就 400（不连库）；凭证不需要挂项目（没有现金流量科目）时给了也 400；没给什么也不做。
        static void CheckFinish()
        {
            CashItemMap given = Map("660399", "07");
            Field("ci unused", "cash_items", delegate { ArapCashItems.Finish(null, given); });
            List<GlLine> lines = new List<GlLine>();
            lines.Add(Gl("100201", 5m, 0m));
            lines.Add(Gl("1122", 0m, 5m));
            List<bool> cash = new List<bool>();
            cash.Add(false);
            cash.Add(false);
            Field("ci no flow lines", "cash_items", delegate { ArapCashItems.Flows(null, lines, cash, 2026, "2026-09-30", Map("1122", "01")); });
            ArapCashItems.Flows(null, lines, cash, 2026, "2026-09-30", new CashItemMap());
            ArapCashItems.Finish(null, null);
            Expect("ci no flows added", lines[0].Flows.Count == 0 && lines[1].Flows.Count == 0);
        }

        static void CheckBadRecovered()
        {
            Expect("ci bad text", ArapVoucherDoc.BadRecoveredText("0000000012", "HZAR0000000003")
                == "收款单 0000000012 已用于坏账收回（处理号 HZAR0000000003），请对坏账收回制单");
            Expect("ci bad sql", ArapVoucherDoc.BadRecoveredSql.IndexOf("cProcStyle=N'9H'", StringComparison.Ordinal) > 0
                && ArapVoucherDoc.BadRecoveredSql.IndexOf("cVouchType=N'48'", StringComparison.Ordinal) > 0);
        }

        static CashItemMap Map(string account, string item)
        {
            CashItemMap map = new CashItemMap();
            map.Items[account] = item;
            return map;
        }

        static Dictionary<string, object> Source(string item, string prefix, string dir)
        {
            Dictionary<string, object> one = new Dictionary<string, object>();
            one["item"] = item;
            one["src"] = prefix;
            one["dir"] = dir;
            return one;
        }

        static GlLine Gl(string account, decimal debit, decimal credit)
        {
            GlLine line = new GlLine();
            line.Account = account;
            line.Debit = debit;
            line.Credit = credit;
            return line;
        }

        static Dictionary<string, object> Body(string json)
        {
            return Json.Parse(Encoding.UTF8.GetBytes(json));
        }

        static void Bad(string name, string value, string field)
        {
            Field(name, field, delegate
            {
                ArapVoucherReq.Parse(Body("{\"flag\":\"AR\",\"type\":\"ar_receipt\",\"id\":5,\"cash_items\":" + value + "}"));
            });
        }

        delegate void Act();

        static void Field(string name, string field, Act act)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                Expect(name + " (" + ex.Field + ")", ex.Status == 400 && ex.Code == "bad_request" && ex.Field == field);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }
    }
}
