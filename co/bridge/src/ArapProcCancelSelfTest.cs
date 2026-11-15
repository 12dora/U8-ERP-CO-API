using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的取消处理部分（arap/process/cancel）：请求校验、锁键、按处理行的闸门和余额归集、SQL 占位符个数。
    // 只跑纯函数，不连库、不建 COM。样例按 U8 处理行的常见形状构造（NoA 应收单对三行采购发票，NoB 应收单对付款单
    // 的 iFlag=1 / iFlag=6 成对行；BZAR…001 应收单 ± 一对行）。
    internal static class ArapProcCancelSelfTest
    {
        const string NoA = "YCFAP000000000001";
        const string NoB = "YCFAP000000900002";

        public static void Run()
        {
            CheckParse();
            CheckLockKeys();
            CheckTransferBills();
            CheckTransferReceipt();
            CheckMerge();
            CheckRefusals();
            CheckIncomplete();
            CheckRed();
            CheckSql();
            // 取消票据处理（PJJ / PJT / PJB / CL，NotesUndo）。
            NotesUndoSelfTest.Run();
            // 应收 / 应付处理记录 arap/process/list（ArapProcListSelfTest；SelfTest.Run 已到 60 行上限，挂在这里）。
            ArapProcListSelfTest.Run();
            // 坏账处理的取消、制单、列表（ArapProcBadSelfTest）。
            ArapProcBadSelfTest.Run();
        }

        static void CheckParse()
        {
            ProcCancelAsk ask = ArapProcCancelReq.Parse(Body("AR", NoA));
            Expect("proc parse 9I", ask.Kind.Style == "9I" && ask.Kind.Both && ask.Kind.Restore && !ask.Kind.FlagDelete);
            ask = ArapProcCancelReq.Parse(Body("AP", "FCYAR000000000001"));
            Expect("proc parse 9J", ask.Kind.Style == "9J" && ask.Kind.Name == "transfer");
            ask = ArapProcCancelReq.Parse(Body("AP", "BZAP000000000002"));
            Expect("proc parse BZ", ask.Kind.Style == "BZ" && !ask.Kind.Both && ask.Kind.Ledgers[0] == "AP" && ask.Kind.FlagDelete);
            Status("proc parse 9J flag", 400, delegate { ArapProcCancelReq.Parse(Body("AR", "FCYAR000000000001")); });
            Status("proc parse BZ flag", 400, delegate { ArapProcCancelReq.Parse(Body("AP", "BZAR000000000001")); });
            Status("proc parse HX", 400, delegate { ArapProcCancelReq.Parse(Body("AR", "HXAR000000000001")); });
            Status("proc parse flag", 400, delegate { ArapProcCancelReq.Parse(Body("GL", NoA)); });
            Status("proc parse tail", 400, delegate { ArapProcCancelReq.Parse(Body("AR", "YCFAP12a")); });
            Expect("proc kind of", ArapProcCancelReq.KindOf("SYRAR000000000001") == null);
        }

        static void CheckLockKeys()
        {
            string[] keys = ArapProcCancelReq.LockKeys(Body("AR", NoA));
            Expect("proc keys 9I", string.Join(",", keys) == "arap:writeoff:AR,arap:writeoff:AP,arap:proc:" + NoA);
            keys = ArapProcCancelReq.LockKeys(Body("AR", "BZAR000000000001"));
            Expect("proc keys BZ", string.Join(",", keys) == "arap:writeoff:AR,arap:proc:BZAR000000000001");
            Expect("proc keys bad", ArapProcCancelReq.LockKeys(Body("AP", NoA)).Length == 0);
        }

        // NoA：应收单贷 100000；采购发票三行借 60000 + 4000 + 36000。只有应收单加回，发票走 ArapUnwriteoffBill。
        static void CheckTransferBills()
        {
            List<ProcRow> rows = new List<ProcRow>();
            rows.Add(Row("AR 9I R0 YS0001 C001", 0m, 100000m, 0, 0, 0));
            rows.Add(Row("AP 9I 01 PI0001 S001", 60000m, 0m, 4869, 0, 0));
            rows.Add(Row("AP 9I 01 PI0001 S001", 4000m, 0m, 4870, 0, 0));
            rows.Add(Row("AP 9I 01 PI0001 S001", 36000m, 0m, 4871, 0, 0));
            ProcCancelPlan plan = ArapProcCancelGate.FromRows(Ask("AR", NoA), rows);
            Expect("proc 9I docs", plan.Docs.Count == 2 && plan.Docs[0].Name == "ar_bill" && plan.Docs[1].Name == "purchase_invoice");
            Expect("proc 9I bills", plan.Bills.Count == 1 && plan.Bills[0].Back == 100000m && plan.Lines.Count == 0);
            Expect("proc 9I invoices", plan.HasCo("AP", "0") && !plan.HasCo("AR", "2"));
            Expect("proc 9I counts", plan.Count("AR") == 1 && plan.Count("AP") == 3 && plan.MaxAuto("AP") == rows[3].Auto);
            CheckTransferItems(plan);
        }

        static void CheckTransferItems(ProcCancelPlan plan)
        {
            Expect("proc 9I later docs", plan.DocKeys("AP").Count == 1 && plan.DocKeys("AP")[0][1] == "PI0001");
            List<object> items = ArapProcCancelBody.Items(plan);
            Dictionary<string, object> second = (Dictionary<string, object>)items[1];
            Expect("proc 9I items", items.Count == 4 && (decimal)second["debit"] == 60000m && (int)second["line_id"] == 4869);
        }

        // NoB：付款单 iFlag=1 借 100000 和 iFlag=6 贷 100000 挂在同一行上；只按 iFlag=1 那条加回。
        static void CheckTransferReceipt()
        {
            List<ProcRow> rows = new List<ProcRow>();
            rows.Add(Row("AR 9I R0 YS0002 C001", 0m, 100000m, 0, 0, 0));
            rows.Add(Row("AP 9I 49 FK0001 S001", 100000m, 0m, 0, 2771, 1));
            rows.Add(Row("AP 9I 49 FK0001 S001", 0m, 100000m, 0, 2771, 6));
            ProcCancelPlan plan = ArapProcCancelGate.FromRows(Ask("AR", NoB), rows);
            Expect("proc 9I receipt line", plan.Lines.Count == 1 && plan.Lines[0].Line == 2771 && plan.Lines[0].Back == 100000m);
            Expect("proc 9I receipt doc", plan.DocOf("AP", "49", "FK0001") != null && plan.DocOf("AP", "49", "FK0001").Title == "付款单");
            Expect("proc within", ArapProcCancelGate.Within(100000m, 100000m, 0.005m) && !ArapProcCancelGate.Within(200000m, 100000m, 0.005m)
                && ArapProcCancelGate.Within(-5m, -10m, 0.005m) && !ArapProcCancelGate.Within(1m, -10m, 0.005m));
        }

        // BZAR…001：同一张应收单原客户 -100、新客户 +100；不加回余额，两个往来单位各一项。
        static void CheckMerge()
        {
            List<ProcRow> rows = new List<ProcRow>();
            rows.Add(Row("AR BZ R0 YS0003 C001", -100m, 0m, 0, 0, 0));
            rows.Add(Row("AR BZ R0 YS0003 C002", 100m, 0m, 0, 0, 0));
            ProcCancelPlan plan = ArapProcCancelGate.FromRows(Ask("AR", "BZAR000000000001"), rows);
            Expect("proc BZ plan", plan.Docs.Count == 1 && plan.Bills.Count == 0 && plan.Lines.Count == 0);
            Expect("proc BZ items", ArapProcCancelBody.Items(plan).Count == 2 && ArapProcCancelBody.Restored(plan).Count == 0);
        }

        static void CheckRefusals()
        {
            Status("proc empty", 404, delegate { ArapProcCancelGate.FromRows(Ask("AR", NoA), new List<ProcRow>()); });
            ProcRow pz = Row("AR 9I R0 YS0001 C001", 0m, 1m, 0, 0, 0);
            pz.Pz = "AR0000000004541";
            Message("proc vouchered", ArapProcCancelGate.Vouchered, delegate { Plan(NoA, pz, ApRow()); });
            Status("proc one side", 409, delegate { Plan(NoA, Row("AR 9I R0 YS0001 C001", 0m, 1m, 0, 0, 0)); });
            Status("proc style", 409, delegate { Plan(NoA, Row("AR 9P R0 YS0001 C001", 0m, 1m, 0, 0, 0), ApRow()); });
            ProcRow wrong = ApRow();
            wrong.Flag = "AR";
            Status("proc ledger flag", 409, delegate { Plan(NoA, Row("AR 9I R0 YS0001 C001", 0m, 1m, 0, 0, 0), wrong); });
            Status("proc type", 409, delegate { Plan(NoA, Row("AR 9I 50 QC0001 C001", 0m, 1m, 0, 0, 0), ApRow()); });
            Status("proc receipt side", 409, delegate { Plan(NoA, Row("AR 9I 49 FK0001 C001", 0m, 1m, 0, 1, 0), ApRow()); });
            ProcRow co = ApRow();
            co.CoCode = "PI0009";
            Status("proc shape", 409, delegate { Plan(NoA, Row("AR 9I R0 YS0001 C001", 0m, 1m, 0, 0, 0), co); });
            ProcRow later = ApRow();
            later.RegDate = "2026-09-01";
            Status("proc dates", 409, delegate { Plan(NoA, Row("AR 9I R0 YS0001 C001", 0m, 1m, 0, 0, 0), later); });
            ProcRow agent = Row("AR BZ R0 YS0003 C001", 1m, 0m, 0, 0, 0);
            agent.BusType = "代理进口";
            Status("proc agent", 409, delegate { Plan("BZAR000000000001", agent); });
        }

        // 加回不了的处理行（收付款单行缺 iCoClosesID、发票行缺 iBVid）一律 409，不能只删行；处理号不存在的 404 带号。
        static void CheckIncomplete()
        {
            Message("proc no close", ArapProcCancelGate.Incomplete,
                delegate { Plan(NoB, Row("AR 9I R0 YS0002 C001", 0m, 1m, 0, 0, 0), Row("AP 9I 49 FK0001 S001", 1m, 0m, 0, 0, 1)); });
            Message("proc no bvid", ArapProcCancelGate.Incomplete,
                delegate { Plan(NoA, Row("AR 9I R0 YS0001 C001", 0m, 1m, 0, 0, 0), Row("AP 9I 01 PI0001 S001", 1m, 0m, 0, 0, 0)); });
            try
            {
                ArapProcCancelGate.FromRows(Ask("AR", NoA), new List<ProcRow>());
                throw new InvalidOperationException("proc empty no");
            }
            catch (BridgeException ex)
            {
                Expect("proc empty no", ex.Status == 404 && ex.Message.IndexOf(NoA, StringComparison.Ordinal) > 0);
            }
        }

        // 红票对冲（9N，实测形状）：应收蓝字发票行 iDAmount -10、红字发票行 +10；应收单蓝字 -5、红字 +5 时加回 +5 / -5。
        // 只写本侧、删除带 cflag；收付款单的 9N 拒绝。
        static void CheckRed()
        {
            ProcCancelAsk ask = Ask("AR", "HRAR0000000000001");
            Expect("proc 9N kind", ask.Kind.Style == "9N" && ask.Kind.Negate && ask.Kind.Restore && ask.Kind.FlagDelete && !ask.Kind.Both);
            Expect("proc 9N ap", Ask("AP", "HPAP0000000000001").Kind.Ledgers[0] == "AP");
            Status("proc 9N flag", 400, delegate { Ask("AP", "HRAR0000000000001"); });
            List<ProcRow> rows = new List<ProcRow>();
            rows.Add(Row("AR 9N 26 SI0002 C001", -10m, 0m, 21, 0, 0));
            rows.Add(Row("AR 9N 26 SI0003 C001", 10m, 0m, 31, 0, 0));
            rows.Add(Row("AR 9N R0 YS0005 C001", -5m, 0m, 0, 0, 0));
            rows.Add(Row("AR 9N R0 YS0006 C001", 5m, 0m, 0, 0, 0));
            ProcCancelPlan plan = ArapProcCancelGate.FromRows(ask, rows);
            Expect("proc 9N bills", plan.Bills.Count == 2 && plan.Bills[0].Back == 5m && plan.Bills[1].Back == -5m && plan.Lines.Count == 0);
            Expect("proc 9N sale", plan.HasCo("AR", "2") && plan.Docs.Count == 4);
            Status("proc 9N receipt", 409, delegate { Plan("HRAR0000000000001", Row("AR 9N 48 SK0001 C001", 0m, 1m, 0, 7, 0)); });
            Expect("proc 9N keys", string.Join(",", ArapProcCancelReq.LockKeys(Body("AP", "HPAP0000000000001")))
                == "arap:writeoff:AP,arap:proc:HPAP0000000000001");
        }

        // 写语句的占位符个数与传的参数一致；「之后的处理」查询 3 个固定参数加每张单据 4 个。
        static void CheckSql()
        {
            string[] texts = ProcCancelSql.WriteTexts("AP");
            int[] counts = ProcCancelSql.WriteArgCounts();
            for (int i = 0; i < texts.Length; i++)
            {
                Expect("proc sql marks " + i, ProcCancelSql.Marks(texts[i]) == counts[i] && texts[i].IndexOf("Ap_Detail", StringComparison.Ordinal) > 0);
            }
            List<ProcRow> rows = new List<ProcRow>();
            rows.Add(Row("AR 9I R0 YS0001 C001", 0m, 1m, 0, 0, 0));
            rows.Add(Row("AR 9I 26 SI0001 C001", 0m, 1m, 11, 0, 0));
            rows.Add(ApRow());
            ProcCancelPlan plan = ArapProcCancelGate.FromRows(Ask("AR", NoA), rows);
            List<object> args = ProcCancelSql.LaterArgs(plan, "AR");
            string cond = UnwriteoffSql.DocArgs(plan.DocKeys("AR"), 0, args);
            string sql = ProcCancelSql.LaterText("AR").Replace("{DOCS}", cond);
            Expect("proc later marks", args.Count == 11 && ProcCancelSql.Marks(sql) == args.Count
                && (int)args[1] == rows[1].Auto && (string)args[2] == NoA);
            Expect("proc 9I sale", plan.HasCo("AR", "2") && plan.Docs[1].Name == "sale_invoice");
        }

        static void Plan(string no, params ProcRow[] rows)
        {
            ProcCancelAsk ask = ArapProcCancelReq.Parse(Body(ArapProcCancelReq.KindOf(no).Flag, no));
            ArapProcCancelGate.FromRows(ask, new List<ProcRow>(rows));
        }

        static ProcRow ApRow()
        {
            return Row("AP 9I 01 PI0001 S001", 1m, 0m, 4869, 0, 0);
        }

        static int _auto;

        // spec：账 处理方式 单据类型 单号 往来单位，空格分隔。
        static ProcRow Row(string spec, decimal df, decimal cf, int bvid, int close, int iflag)
        {
            string[] p = spec.Split(' ');
            string ledger = p[0];
            string type = p[2];
            string code = p[3];
            ProcRow row = new ProcRow();
            row.Ledger = ledger;
            row.Auto = ++_auto;
            row.Style = p[1];
            row.Flag = ledger;
            row.VType = type;
            row.VCode = code;
            row.CoType = type;
            row.CoCode = code;
            row.BVid = bvid;
            row.CoClose = close;
            row.IFlag = iflag;
            row.Period = 8;
            row.RegDate = "2026-08-28";
            row.Pz = "";
            row.Contract = "";
            row.BusType = "";
            row.DF = df;
            row.CF = cf;
            row.Head = new Dictionary<string, object>();
            row.Head["cDwCode"] = p[4];
            return row;
        }

        static ProcCancelAsk Ask(string flag, string no)
        {
            return ArapProcCancelReq.Parse(Body(flag, no));
        }

        static Dictionary<string, object> Body(string flag, string no)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["flag"] = flag;
            body["cancel_no"] = no;
            return body;
        }

        static void Status(string name, int status, Action act)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                Expect(name + " status", ex.Status == status);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void Message(string name, string message, Action act)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                Expect(name + " message", ex.Status == 409 && ex.Message == message);
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
