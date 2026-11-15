using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的采购手工结算部分（PuSettleSelfTest.Run 调用）：请求解析、结算行的金额与暂估、状态规则（同号、未结算数量、
    // 对冲合计为 0、同一供应商、同一存货）、写入 SQL 的占位符个数与参数、预演模式、功能 id、meta。只跑纯函数，不连库。
    internal static class PuSettleManSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckPairs();
            CheckOffsets();
            CheckRules();
            CheckSql();
            CheckWiring();
        }

        static void CheckParse()
        {
            List<ManLine> lines = PuSettleManReq.Parse(new object[] { Line(11, 21, 2m), Line(12, 0, -1), Line(0, 22, 3.5) });
            Expect("man parse", All(lines.Count == 3, lines[0].InId == 11, lines[0].BsId == 21, lines[0].Qty == 2m,
                lines[1].BsId == 0, lines[1].Qty == -1m, lines[2].InId == 0, lines[2].Qty == 3.5m, lines[2].Index == 2));
            Dictionary<string, object> amount = Line(11, 21, 2);
            amount["amount"] = 10.5;
            Expect("man amount", PuSettleManReq.Parse(new object[] { amount })[0].HasAmount);
            Bad("man no ids", Line(0, 0, 1), "lines.0");
            Bad("man zero qty", Line(1, 2, 0), "lines.0.quantity");
            Bad("man bool id", Map("in_line_id", true, "quantity", 1), "lines.0.in_line_id");
            Bad("man neg id", Map("in_line_id", -3, "quantity", 1), "lines.0.in_line_id");
            Bad("man unknown", Map("in_line_id", 1, "quantity", 1, "cMemo", "x"), "lines.0.cMemo");
            Bad("man no qty", Map("in_line_id", 1), "lines.0.quantity");
            Bad("man rd amount", Map("in_line_id", 1, "quantity", 1, "amount", 5), "lines.0.amount");
            Bad("man amount sign", Map("in_line_id", 1, "invoice_line_id", 2, "quantity", 1, "amount", -5), "lines.0.amount");
            Bad("man qty text", Map("in_line_id", 1, "quantity", "1"), "lines.0.quantity");
            try
            {
                PuSettleManReq.Parse(new object[] { Line(1, 2, 1), Line(1, 2, 1) });
                throw new InvalidOperationException("man dup");
            }
            catch (BridgeException ex)
            {
                Expect("man dup", All(ex.Status == 400, ex.Field == "lines.1"));
            }
            object[] many = new object[PuSettleReq.LinesMax + 1];
            for (int i = 0; i < many.Length; i++)
            {
                many[i] = Line(i + 1, 0, 1);
            }
            Refused("man too many", delegate { PuSettleManReq.Parse(many); }, 400, "lines");
        }

        // 配对：发票 10 个、金额 100.00、税 13.00；入库 10 个、暂估单价 9、暂估金额 90。先结 3 个再结剩下 7 个。
        static void CheckPairs()
        {
            Dictionary<int, ManRd> rds = Rds(Rd(11, 10m, 0m, 9m, 90m, "A"));
            Dictionary<int, ManBs> bss = Bss(Bs(21, 10m, 100m, 10m, 13m, "A"));
            List<ManRow> rows = PuSettleManPlan.Compute(Lines(Line(11, 21, 3)), rds, bss, 6);
            ManRow a = rows[0];
            Expect("man pair part", All(a.Money == 30m, a.Cost == 10m, a.Tax == 3.9m, a.Sum == 33.9m, a.ACost == 9m,
                a.APrice == 27m));
            rds[11].SQty = 3m;
            rds[11].PriorAPrice = 27m;
            bss[21].PriorQty = 3m;
            bss[21].PriorMoney = 30m;
            bss[21].PriorTax = 3.9m;
            List<ManRow> rest = PuSettleManPlan.Compute(PuSettleManReq.Parse(new object[] { Line(11, 21, 7) }), rds, bss, 6);
            ManRow b = rest[0];
            Expect("man pair last", All(b.Money == 70m, b.Tax == 9.1m, b.APrice == 63m, b.Sum == 79.1m));
            Dictionary<int, ManRd> whole = Rds(Rd(11, 10m, 0m, 9m, 90.01m, "A"));
            ManRow c = PuSettleManPlan.Compute(PuSettleManReq.Parse(new object[] { Line(11, 21, 10) }), whole,
                Bss(Bs(21, 10m, 100m, 10m, 13m, "A")), 6)[0];
            Expect("man pair whole", All(c.Money == 100m, c.Cost == 10m, c.APrice == 90.01m, c.Tax == 13m));
            Dictionary<string, object> given = Line(11, 21, 3);
            given["amount"] = 31;
            ManRow d = PuSettleManPlan.Compute(PuSettleManReq.Parse(new object[] { given }),
                Rds(Rd(11, 10m, 0m, 9m, 90m, "A")), Bss(Bs(21, 10m, 100m, 10m, 13m, "A")), 6)[0];
            Expect("man pair amount", All(d.Money == 31m, d.Cost == 10.333333m));
            ManRd bare = Rd(11, 10m, 0m, 0m, 0m, "A");
            bare.HasACost = false;
            ManRow e = PuSettleManPlan.Compute(PuSettleManReq.Parse(new object[] { Line(11, 21, 3) }), Rds(bare),
                Bss(Bs(21, 10m, 100m, 10m, 13m, "A")), 6)[0];
            Expect("man pair no acost", All(e.ACost == 10m, e.APrice == 30m));
        }

        // 红蓝入库对冲（暂估）与红蓝发票对冲（发票金额，没有暂估和税）。
        static void CheckOffsets()
        {
            Dictionary<int, ManRd> rds = Rds(Rd(11, 2m, 0m, 5m, 10m, "A"), Rd(12, -2m, 0m, 5m, -10m, "A"));
            List<ManRow> rd = PuSettleManPlan.Compute(PuSettleManReq.Parse(new object[] { Line(11, 0, 2), Line(12, 0, -2) }),
                rds, new Dictionary<int, ManBs>(), 6);
            Expect("man rd offset", All(rd[0].Money == 10m, rd[0].Cost == 5m, rd[1].Money == -10m,
                rd[1].APrice == -10m, rd[0].Tax == 0m, rd[1].Sum == 0m));
            Dictionary<int, ManBs> bss = Bss(Bs(21, -2m, -6m, 3m, 0m, "B"), Bs(22, 2m, 5.4m, 2.7m, 0.7m, "B"));
            List<ManRow> bs = PuSettleManPlan.Compute(PuSettleManReq.Parse(new object[] { Line(0, 21, -2), Line(0, 22, 2) }),
                new Dictionary<int, ManRd>(), bss, 6);
            Expect("man inv offset", All(bs[0].Money == -6m, bs[0].Cost == 3m, bs[1].Money == 5.4m, bs[1].Tax == 0m,
                bs[1].Sum == 0m, bs[1].APrice == 0m));
            List<ManRdSum> sums = PuSettleManPlan.RdSums(rd);
            Expect("man rd sums", All(sums.Count == 2, sums[1].Qty == -2m, sums[1].APrice == -10m));
            Expect("man bs sums", PuSettleManPlan.BsSums(bs).Count == 2);
        }

        static void CheckRules()
        {
            Dictionary<int, ManRd> rds = Rds(Rd(11, 10m, 4m, 9m, 90m, "A"), Rd(12, -2m, 0m, 5m, -10m, "A"));
            Dictionary<int, ManBs> bss = Bss(Bs(21, 10m, 100m, 10m, 13m, "A"), Bs(22, 5m, 50m, 10m, 0m, "C"));
            Rules("man ok", Lines(Line(11, 21, 6)), rds, bss, 0);
            Rules("man over rd", Lines(Line(11, 21, 7)), rds, bss, 409);
            Rules("man sign", Lines(Line(11, 21, -1)), rds, bss, 409);
            Rules("man inv code", Lines(Line(11, 22, 1)), rds, bss, 409);
            Rules("man rd net", Lines(Line(12, 0, -2)), rds, bss, 409);
            Rules("man missing", Lines(Line(99, 21, 1)), rds, bss, 400);
            Rules("man decimals", Lines(Line(11, 21, 1.005)), rds, bss, 400);
            rds[11].Row["cVenCode"] = "V2";
            Rules("man vendor", Lines(Line(11, 21, 1)), rds, bss, 409);
            rds[11].Row["cVenCode"] = "V1";
            bss[21].Row["cPBVVerifier"] = "op001";
            Rules("man ap verified", Lines(Line(11, 21, 1)), rds, bss, 409);
            bss[21].Row["cPBVVerifier"] = "";
            bss[21].Row["Exch"] = "USD";
            Rules("man currency", Lines(Line(11, 21, 1)), rds, bss, 409);
            bss[21].Row["Exch"] = "人民币";
            Rules("man rmb", Lines(Line(11, 21, 1)), rds, bss, 0);
            CheckMixing();
        }

        // 同一发票行混用红蓝发票对冲与配对：本单内 400，与已有结算 409。
        static void CheckMixing()
        {
            Dictionary<int, ManRd> rds = Rds(Rd(11, 10m, 0m, 9m, 90m, "A"));
            Dictionary<int, ManBs> bss = Bss(Bs(21, 10m, 100m, 10m, 13m, "A"), Bs(22, -2m, -20m, 10m, -2.6m, "A"));
            Rules("man mix in request", Lines(Line(0, 21, 2), Line(0, 22, -2), Line(11, 21, 1)), rds, bss, 400);
            bss[21].PriorOffRows = 1;
            Rules("man mix prior off", Lines(Line(11, 21, 1)), rds, bss, 409);
            bss[21].PriorOffRows = 0;
            bss[21].PriorPairRows = 1;
            Rules("man mix prior pair", Lines(Line(0, 21, 2), Line(0, 22, -2)), rds, bss, 409);
            bss[21].PriorPairRows = 0;
            Rules("man offset ok", Lines(Line(0, 21, 2), Line(0, 22, -2)), rds, bss, 0);
        }

        static void CheckSql()
        {
            ManPlan plan = Plan(PuSettleManPlan.Compute(Lines(Line(11, 21, 3)), Rds(Rd(11, 10m, 0m, 9m, 90m, "A")),
                Bss(Bs(21, 10m, 100m, 10m, 13m, "A")), 6));
            string insert = PuSettleManSql.Expand(PuSettleManSql.LineSql);
            Expect("man line sql", All(insert.IndexOf("{C:", StringComparison.Ordinal) < 0,
                insert.Contains("CASE WHEN r.AutoID IS NULL THEN b.cFree10 ELSE r.cFree10 END"),
                Marks(insert) == PuSettleManSql.LineArgs(plan.Rows[0], 5, 7).Length));
            Expect("man head sql", All(Marks(PuSettleManSql.HeadSql) == PuSettleManSql.HeadArgs(plan, 7, "张三").Length,
                PuSettleManSql.Code(7) == "000000000000007"));
            object[] back = PuSettleManSql.WriteBackArgs(plan, PuSettleManPlan.RdSums(plan.Rows)[0]);
            Expect("man writeback sql", All(Marks(PuSettleManSql.WriteBackSql) == back.Length, (string)back[0] == "3",
                PuInv.Num((string)back[2]) == 30m, (string)back[3] == "9", (string)back[5] == "10",
                (string)back[6] == "11"));
            Dictionary<int, ManRd> done = Rds(Rd(11, 10m, 4m, 9m, 90m, "A"));
            ManPlan rest = Plan(PuSettleManPlan.Compute(Lines(Line(11, 21, 6)), done, Bss(Bs(21, 10m, 100m, 10m, 13m, "A")), 6));
            object[] last = PuSettleManSql.WriteBackArgs(rest, PuSettleManPlan.RdSums(rest.Rows)[0]);
            Expect("man writeback check", All((string)last[0] == "6", (string)last[5] == "6"));
            Expect("man id lock", All(PuSettleManSql.IdTakenText == "结算单号冲突，请重试"));
            Expect("man stamp sql", All(Marks(PuSettleManSql.BillLineSql) == 3, Marks(PuSettleManSql.InDateSql) == 5,
                Marks(PuSettleManSql.BillHeadSql) == 3, Marks(PuSettleManSql.InvCostSql) == 1));
            Expect("man in list", PuSettleManGate.InList("a in ({IN}) x{L}", 3, true) == "a in (?,?,?) x with (updlock, holdlock)");
        }

        static void CheckWiring()
        {
            Expect("man dry create", DryRunModes.Lookup("vouchers/create", PuSettleRead.KindName, "create", "")
                == DryRunModes.Rollback);
            PermRule rule = PermRegistry.ForKey(PuSettleManReq.CreateRule);
            Expect("man rule", All(rule != null, rule.Auths.Length == 1, rule.Auths[0] == "PU040302",
                rule.Objs.Length == 6));
            Dictionary<string, object> spec = MetaWritable.Of(Kinds.Find(PuSettleRead.KindName))["create"] as Dictionary<string, object>;
            Expect("man meta", All(spec != null, (int)spec["lines_max"] == PuSettleReq.LinesMax,
                (int)spec["lines_min"] == 1));
            WorkItem item = new WorkItem();
            item.Type = Kinds.Find(PuSettleRead.KindName);
            Expect("man handles", All(PuSettleManReq.Handles(item, "/u8co/v1/vouchers/create"),
                !PuSettleManReq.Handles(item, "/u8co/v1/vouchers/generate")));
            item.Path = "/u8co/v1/vouchers/create";
            Expect("man locks", Array.IndexOf(DocLocks.KeysOf(item), "new:purchase_settle") >= 0);
        }

        static void Rules(string name, List<ManLine> lines, Dictionary<int, ManRd> rds, Dictionary<int, ManBs> bss, int status)
        {
            ManPlan plan = new ManPlan();
            plan.Date = "2026-09-30";
            plan.QuanDec = 2;
            plan.CostDec = 6;
            plan.Lines = lines;
            try
            {
                PuSettleManRules.Check(plan, rds, bss);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == status);
                return;
            }
            Expect(name, status == 0);
        }

        static ManPlan Plan(List<ManRow> rows)
        {
            ManPlan plan = new ManPlan();
            plan.Date = "2026-09-30";
            plan.QuanDec = 2;
            plan.CostDec = 6;
            plan.Rows = rows;
            plan.PtCode = "01";
            plan.Ven = "V1";
            return plan;
        }

        static ManRd Rd(int id, decimal qty, decimal sqty, decimal acost, decimal aprice, string inv)
        {
            ManRd rd = new ManRd();
            rd.Id = id;
            rd.RdId = id * 10;
            rd.InvCode = inv;
            rd.Qty = qty;
            rd.SQty = sqty;
            rd.HasACost = true;
            rd.ACost = acost;
            rd.APrice = aprice;
            rd.Row = Row("cVenCode", "V1", "cBusType", "普通采购", "cHandler", "张三", "RdDate", "2026-09-01");
            return rd;
        }

        static ManBs Bs(int id, decimal qty, decimal money, decimal cost, decimal tax, string inv)
        {
            ManBs bs = new ManBs();
            bs.Id = id;
            bs.Pbvid = id * 10;
            bs.InvCode = inv;
            bs.Qty = qty;
            bs.Money = money;
            bs.Cost = cost;
            bs.Tax = tax;
            bs.Row = Row("cVenCode", "V1", "cBusType", "普通采购", "cVerifier", "张三", "BillDate", "2026-09-02");
            foreach (KeyValuePair<string, object> pair in Row("First", "0", "Orig", "0", "Paid", "0", "cPBVBillType", "01"))
            {
                bs.Row[pair.Key] = pair.Value;
            }
            bs.Row["UpSo"] = "RD";
            return bs;
        }

        static Dictionary<int, ManRd> Rds(params ManRd[] list)
        {
            Dictionary<int, ManRd> map = new Dictionary<int, ManRd>();
            foreach (ManRd rd in list)
            {
                map[rd.Id] = rd;
            }
            return map;
        }

        static Dictionary<int, ManBs> Bss(params ManBs[] list)
        {
            Dictionary<int, ManBs> map = new Dictionary<int, ManBs>();
            foreach (ManBs bs in list)
            {
                map[bs.Id] = bs;
            }
            return map;
        }

        static List<ManLine> Lines(params Dictionary<string, object>[] rows)
        {
            return PuSettleManReq.Parse(rows);
        }

        static Dictionary<string, object> Line(int rd, int bs, object qty)
        {
            return Map("in_line_id", rd, "invoice_line_id", bs, "quantity", qty);
        }

        static Dictionary<string, object> Row(params string[] pairs)
        {
            Dictionary<string, object> row = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                row[pairs[i]] = pairs[i + 1];
            }
            return row;
        }

        static Dictionary<string, object> Map(params object[] pairs)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                row[(string)pairs[i]] = pairs[i + 1];
            }
            return row;
        }

        static int Marks(string sql)
        {
            int n = 0;
            foreach (char ch in sql)
            {
                if (ch == '?')
                {
                    n++;
                }
            }
            return n;
        }

        static void Bad(string name, Dictionary<string, object> line, string field)
        {
            Refused(name, delegate { PuSettleManReq.Parse(new object[] { line }); }, 400, field);
        }

        static void Refused(string name, Action call, int status, string field)
        {
            try
            {
                call();
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == status && ex.Field == field);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static bool All(params bool[] checks)
        {
            return Array.TrueForAll(checks, delegate(bool ok) { return ok; });
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
