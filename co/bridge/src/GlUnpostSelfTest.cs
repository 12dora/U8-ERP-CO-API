using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的取消记账部分（GlUnpost）：只测纯逻辑（请求校验、与最近一次记账范围的核对、SQL 的参数个数和年度条件、
    // 锁键、权限登记），不连库。
    internal static class GlUnpostSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckMatch();
            CheckSql();
            Expect("rule", PermRegistry.ForKey(GlUnpostReq.Rule) != null);
            string[] keys = GlPostParse.LockKeys(new Dictionary<string, object>(), "2026");
            Expect("lock", keys.Length == 1 && keys[0] == "gl:post");
        }

        static Dictionary<string, object> Voucher(string sign, object no)
        {
            Dictionary<string, object> v = new Dictionary<string, object>();
            v["sign"] = sign;
            v["no"] = no;
            return v;
        }

        static void CheckParse()
        {
            GlUnpostAsk ask = GlUnpostReq.Parse(new Dictionary<string, object>(), 2026);
            Expect("empty", ask.Year == 2026 && ask.Period == 0 && ask.Expect == null);
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["fiscal_year"] = 2025;
            body["period"] = 9;
            ask = GlUnpostReq.Parse(body, 2026);
            Expect("year period", ask.Year == 2025 && ask.Period == 9 && ask.Expect == null);
            body["vouchers"] = new object[] { Voucher("转", 3) };
            ask = GlUnpostReq.Parse(body, 2026);
            Expect("vouchers", ask.Expect != null && ask.Expect.Count == 1 && ask.Expect[0].No == 3 && ask.Period == 9);
            Dictionary<string, object> bad = new Dictionary<string, object>();
            bad["period"] = 13;
            Bad("period range", bad);
            bad = new Dictionary<string, object>();
            bad["vouchers"] = new object[] { Voucher("转", 3) };
            Bad("vouchers no period", bad);
            bad["period"] = 9;
            bad["vouchers"] = new object[0];
            Bad("vouchers empty", bad);
            bad["vouchers"] = new object[] { Voucher("转", 3), Voucher("转", 3) };
            Bad("vouchers dup", bad);
        }

        static GlUnpostAsk Batch(params int[] nos)
        {
            GlUnpostAsk ask = new GlUnpostAsk();
            ask.Year = 2026;
            foreach (int no in nos)
            {
                GlUnpostItem item = new GlUnpostItem();
                item.Period = 9;
                item.Seq = 3;
                item.No = no;
                item.Sign = "转";
                ask.Batch.Add(item);
            }
            return ask;
        }

        static void CheckMatch()
        {
            GlUnpostReq.Match(Batch(3, 4), 9);
            GlUnpostAsk ask = Batch(3, 4);
            ask.Period = 8;
            Refused("period", ask);
            ask = Batch(3, 4);
            ask.Expect = new List<GlPostItem>();
            ask.Expect.Add(Item("转", 3));
            Refused("subset", ask);
            ask.Expect.Add(Item("转", 4));
            GlUnpostReq.Match(ask, 9);
            ask.Expect.Add(Item("转", 5));
            Refused("superset", ask);
        }

        static GlPostItem Item(string sign, int no)
        {
            GlPostItem item = new GlPostItem();
            item.Sign = sign;
            item.No = no;
            return item;
        }

        // 参数个数与 GlUnpost.Reverse / GlUnpostCheck 传的实参一致；每条写都带年度。
        static void CheckSql()
        {
            foreach (bool multi in new bool[] { true, false })
            {
                Count("sum period", GlUnpostSql.SumPeriod(multi), 6);
                Count("sum later", GlUnpostSql.SumLater(multi), 6);
                Count("ass period", GlUnpostSql.AssPeriod(multi), 4);
                Count("ass later", GlUnpostSql.AssLater(multi), 4);
                Expect("ass table", GlUnpostSql.AssPeriod(multi).IndexOf(multi ? "GL_AccMultiAss" : "GL_accass", StringComparison.Ordinal) > 0);
            }
            Expect("sum exch", GlUnpostSql.SumPeriod(true).IndexOf("ISNULL(t.ccexch_name", StringComparison.Ordinal) > 0
                && GlUnpostSql.SumPeriod(false).IndexOf("ISNULL(t.ccexch_name", StringComparison.Ordinal) < 0);
            Count("vouch", GlUnpostSql.VouchSql, 2);
            Count("left", GlUnpostSql.LeftSql, 2);
            Count("clear", GlUnpostSql.ClearSql, 1);
            Count("check sum", GlUnpostCheck.SumText(), 11);
            Count("check ass", GlUnpostCheck.AssText(false), 11);
            Count("check multi", GlUnpostCheck.AssText(true), 11);
            Count("check chain", GlUnpostCheck.ChainText(), 4);
            Expect("vouch year", GlUnpostSql.VouchSql.IndexOf("v.iyear=?", StringComparison.Ordinal) > 0
                && GlUnpostSql.Batch("v").IndexOf("m.iyear=v.iyear", StringComparison.Ordinal) > 0);
        }

        static void Count(string name, string sql, int want)
        {
            int n = 0;
            foreach (char c in sql)
            {
                if (c == '?')
                {
                    n++;
                }
            }
            Expect("params " + name, n == want && sql.IndexOf("iyear", StringComparison.Ordinal) > 0);
        }

        static void Refused(string name, GlUnpostAsk ask)
        {
            try
            {
                GlUnpostReq.Match(ask, 9);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 409 && ex.Code == "state_mismatch");
                return;
            }
            throw new InvalidOperationException("glunpost match " + name);
        }

        static void Bad(string name, Dictionary<string, object> body)
        {
            try
            {
                GlUnpostReq.Parse(body, 2026);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request");
                return;
            }
            throw new InvalidOperationException("glunpost " + name);
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("glunpost " + name);
            }
        }
    }
}
