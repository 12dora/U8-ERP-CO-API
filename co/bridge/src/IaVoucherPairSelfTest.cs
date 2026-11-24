using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的存货核算制单：销售出库单 32（对方科目列、表头回写、缺省摘要）与调拨等成对业务（制单单元分组、
    // 业务单号、两边金额相等、缺省摘要）。不连库、不建 COM。
    internal static class IaVoucherPairSelfTest
    {
        const string Unit = "调拨\u0001DEMO-001";

        public static void Run()
        {
            CheckSaleOut();
            CheckUnits();
            CheckNet();
            CheckDigest();
            CheckOutId();
            CheckPending();
            CheckStrays();
        }

        // 32：出库、对方科目取「对方科目」cOppHead、写来源表头 cPZID、不写发出商品明细；SQL 连 RdRecords32 并取业务单号。
        static void CheckSaleOut()
        {
            IaType t = IaType.Find("32");
            Expect("iv32 type", All(t != null, !t.In, t.Opp == "opp", t.Table == "32", t.MarkHead, !t.En));
            Expect("iv32 opp col", IaVoucherAcct.OppColumn(Row("32", "", 0)) == "o_opp");
            string sql = IaVoucherLoad.Sql(t);
            Expect("iv32 sql", All(sql.IndexOf("RdRecords32", StringComparison.Ordinal) > 0, sql.IndexOf("cBusCode", StringComparison.Ordinal) > 0));
            Expect("iv32 digest", IaVoucherPlan.Digest("", Row("32", "", 0)) == "销售出库单");
        }

        // 成对业务同键的 08、09 一个单元（保持先后），其他单据各自一个。
        static void CheckUnits()
        {
            IaBill a = Bill("08", 1, Row("08", "调拨", 100));
            IaBill b = Bill("11", 2, Row("11", "", 5));
            IaBill c = Bill("09", 3, Row("09", "调拨", 100));
            IaBill d = Bill("11", 4, Row("11", "", 5));
            List<List<IaBill>> units = IaVoucherPair.Units(new List<IaBill>(new IaBill[] { a, b, c, d }));
            Expect("iv pair units", All(units.Count == 3, units[0].Count == 2, units[0][0] == a, units[0][1] == c, units[1][0] == b,
                units[2][0] == d));
            IaRow lower = Row("09", "调拨", 1);
            lower.BusCode = "demo-001";
            Expect("iv pair key", All(a.Unit == Unit, b.Unit == "", IaVoucherPair.Key(Row("09", "转换", 1)) == "转换\u0001DEMO-001",
                IaVoucherPair.Key(lower) == Unit));
        }

        // 两边金额相等才制单；不等、缺业务单号、行与单据的业务不一致时拒绝；不是成对业务不查。
        static void CheckNet()
        {
            List<IaBill> pair = Pair(100, 100);
            Expect("iv pair net ok", IaVoucherPair.Problem(pair) == null);
            string uneven = IaVoucherPair.Problem(Pair(100, 90));
            Expect("iv pair net", All(uneven != null, uneven != null && uneven.StartsWith("调拨单 DEMO-001", StringComparison.Ordinal)));
            Expect("iv pair one side", IaVoucherPair.Problem(new List<IaBill>(new IaBill[] { Pair(100, 100)[0] })) != null);
            string zero = IaVoucherPair.Problem(new List<IaBill>(new IaBill[] { Pair(0, 0)[1] }));
            Expect("iv pair one side zero", zero != null && zero.IndexOf("缺少另一边", StringComparison.Ordinal) > 0);
            List<IaBill> mixed = Pair(100, 100);
            mixed[1].Rows[0].BusCode = "DEMO-002";
            Expect("iv pair mixed", IaVoucherPair.Problem(mixed) != null);
            List<IaBill> blank = Pair(100, 100);
            foreach (IaBill bill in blank)
            {
                bill.Rows[0].BusCode = "";
                bill.Unit = IaVoucherPair.Key(bill.Rows[0]);
            }
            Expect("iv pair no code", IaVoucherPair.Problem(blank) != null);
            Expect("iv pair plain", IaVoucherPair.Problem(new List<IaBill>(new IaBill[] { Bill("11", 2, Row("11", "", 5)) })) == null);
        }

        // 成对业务缺省摘要是业务单据名；有备注时取备注，调用方给了就用它。
        static void CheckDigest()
        {
            IaRow move = Row("08", "调拨", 1);
            IaRow form = Row("09", "转换", 1);
            Expect("iv pair digest", All(IaVoucherPlan.Digest("", move) == "调拨单", IaVoucherPlan.Digest("", form) == "形态转换单",
                IaVoucherPlan.Digest("", Row("08", "组装", 1)) == "组装单", IaVoucherPlan.Digest("", Row("09", "拆卸", 1)) == "拆卸单"));
            move.Memo = "文本A";
            Expect("iv pair digest memo", All(IaVoucherPlan.Digest("", move) == "文本A", IaVoucherPlan.Digest("DEMO-001", form) == "DEMO-001"));
        }

        // 凭证分录的 coutid：成对业务取业务单号（同 U8），其他取单据号。
        static void CheckOutId()
        {
            IaRow move = Row("08", "调拨", 1);
            move.Code = "DEMO-002";
            IaRow plain = Row("11", "", 1);
            plain.Code = "DEMO-002";
            Expect("iv pair out id", All(IaVoucherPair.OutId(move) == "DEMO-001", IaVoucherPair.OutId(plain) == "DEMO-002"));
        }

        // 未制单、留到下次的单据每个制单单元列一张。
        static void CheckPending()
        {
            IaJob job = new IaJob();
            IaPlan plan = new IaPlan();
            plan.Bills = Pair(100, 100);
            plan.Bills.Add(Bill("11", 2, Row("11", "", 5)));
            job.Plans.Add(plan);
            job.Later = Pair(100, 100);
            Expect("iv pair pending", All(job.Pending().Count == 3, job.Deferred().Count == 1));
        }

        // 按月份选：成对单元的另一边记在晚于制单日期的月份时整个单元跳过；同月的不跳过；按单据选不查。
        static void CheckStrays()
        {
            IaJob job = new IaJob();
            job.Ask = new IaVoucherAsk();
            job.Ask.Month = 3;
            job.Year = 2026;
            job.Date = "2026-03-31";
            List<IaBill> pair = Pair(100, 100);
            pair[0].Rows[0].Month = 3;
            pair[1].Rows[0].Month = 4;
            IaBill plain = Bill("11", 2, Row("11", "", 5));
            plain.Rows[0].Month = 3;
            job.Bills = new List<IaBill>(pair);
            job.Bills.Add(plain);
            Expect("iv pair stray", All(IaVoucherPair.Strays(null, job) == 1, pair[1].Skip.IndexOf("4 月", StringComparison.Ordinal) > 0,
                pair[0].Skip.StartsWith("单据 ", StringComparison.Ordinal), plain.Skip == ""));
            pair[1].Rows[0].Month = 3;
            pair[0].Skip = "";
            pair[1].Skip = "";
            Expect("iv pair stray same", IaVoucherPair.Strays(null, job) == 0);
            pair[1].Rows[0].Month = 4;
            job.Date = "2027-01-15";
            Expect("iv pair stray other year", IaVoucherPair.Strays(null, job) == 0 && pair[1].Skip == "");
            job.Date = "2026-03-31";
            job.Ask.Month = 0;
            Expect("iv pair stray ids", IaVoucherPair.Strays(null, job) == 0);
        }

        static List<IaBill> Pair(decimal inAmount, decimal outAmount)
        {
            return new List<IaBill>(new IaBill[] { Bill("08", 1, Row("08", "调拨", inAmount)), Bill("09", 3, Row("09", "调拨", outAmount)) });
        }

        static IaBill Bill(string type, int id, IaRow row)
        {
            IaBill bill = new IaBill();
            bill.Type = IaType.Find(type);
            bill.Id = id;
            bill.Code = "DEMO-001";
            bill.Rows.Add(row);
            bill.Unit = IaVoucherPair.Key(row);
            return bill;
        }

        static IaRow Row(string type, string pair, decimal amount)
        {
            IaRow row = new IaRow();
            row.Type = IaType.Find(type);
            row.Business = pair.Length > 0 ? pair + (row.Type.In ? "入库" : "出库") : "";
            row.Pair = IaType.PairOf(type, row.Business);
            row.BusCode = "DEMO-001";
            row.Amount = amount;
            return row;
        }

        static bool All(params bool[] checks)
        {
            return Array.IndexOf(checks, false) < 0;
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
