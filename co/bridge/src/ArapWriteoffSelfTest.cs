using System;
using System.Collections.Generic;
using System.Xml;

namespace U8Co
{
    // --selftest 的核销部分：外币核销报文的币种、汇率和本币金额，自动核销按币种配对，
    // include_prepay 的请求校验，外币核销批次的取消闸门。只跑纯函数，不连库、不建 COM。
    internal static class ArapWriteoffSelfTest
    {
        public static void Run()
        {
            CheckFxXml();
            CheckFxTail();
            CheckHomeXml();
            CheckAllocate();
            CheckPrepayFlag();
            CheckUnwriteoffFx();
        }

        // 样例：收款单美元汇率 7.1235（发票汇率与收款单不同时 U8 照样按收款单汇率核销）；10.50 + 30.50 = 41.00，
        // 本币合计 292.06，逐个折算 74.80 + 217.27 比合计多 0.01，尾差并到第一个 vouch（74.79）。
        static void CheckFxXml()
        {
            WriteoffPlan plan = Plan("美元", 7.1235000000m, new decimal[] { 10.50m, 30.50m });
            XmlElement close = Close(plan);
            Expect("writeoff fx close", Attr(close, "cexchname") == "美元" && Attr(close, "iexchrate") == "7.1235"
                && Attr(close, "ijsdhxamount_f") == "41.00" && Attr(close, "ijsdhxamount") == "292.06");
            XmlNodeList vouch = close.SelectNodes("vouch");
            Expect("writeoff fx vouch count", vouch.Count == 2);
            XmlElement first = (XmlElement)vouch[0];
            XmlElement second = (XmlElement)vouch[1];
            Expect("writeoff fx vouch 1", Attr(first, "cexchname") == "美元" && Attr(first, "iexchrate") == "7.1235"
                && Attr(first, "icancelamount_f") == "10.50" && Attr(first, "icancelamount") == "74.79");
            Expect("writeoff fx vouch 2", Attr(second, "icancelamount_f") == "30.50" && Attr(second, "icancelamount") == "217.27");
        }

        // 样例：收款单汇率 7.1065；12.50 + 3.50 + 100.50 = 116.50，本币合计 827.91；逐个折算 88.83、24.87、714.20
        // 比合计少 0.01（第一项 88.83125 舍成 .83），尾差补到第一项得 88.84。
        static void CheckFxTail()
        {
            XmlElement close = Close(Plan("美元", 7.1065m, new decimal[] { 12.50m, 3.50m, 100.50m }));
            XmlNodeList vouch = close.SelectNodes("vouch");
            Expect("writeoff fx tail close", Attr(close, "ijsdhxamount") == "827.91" && Attr(close, "iexchrate") == "7.1065");
            Expect("writeoff fx tail vouch", vouch.Count == 3 && Attr((XmlElement)vouch[0], "icancelamount") == "88.84"
                && Attr((XmlElement)vouch[1], "icancelamount") == "24.87"
                && Attr((XmlElement)vouch[2], "icancelamount") == "714.20");
        }

        // 本位币汇率 1：本币等于原币，汇率写 "1"。
        static void CheckHomeXml()
        {
            WriteoffPlan plan = Plan("人民币", 1m, new decimal[] { 6m, 0.5m });
            XmlElement close = Close(plan);
            XmlElement first = (XmlElement)close.SelectSingleNode("vouch");
            Expect("writeoff home close", Attr(close, "iexchrate") == "1" && Attr(close, "ijsdhxamount") == "6.50"
                && Attr(close, "ijsdhxamount_f") == "6.50");
            Expect("writeoff home vouch", Attr(first, "iexchrate") == "1" && Attr(first, "icancelamount") == "6.00");
            Expect("writeoff rate text", WriteoffXml.Rate(7.1000000000m) == "7.1" && WriteoffXml.Home(0.125m, 1m) == 0.13m);
        }

        // 美元行只冲美元单据（单据汇率不比），人民币行只冲人民币单据；上限按原币累计。
        static void CheckAllocate()
        {
            List<AutoBatch> plan = ArapAutoWriteoffPlan.Allocate(Receipts(), Targets(), 0m);
            Expect("auto fx batches", Describe(plan) == "1:T2=4,T3=6;2:T1=3");
            plan = ArapAutoWriteoffPlan.Allocate(Receipts(), Targets(), 5m);
            Expect("auto fx cap", Describe(plan) == "1:T2=4,T3=1");
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["cur"] = "";
            Expect("auto key home", ArapAutoWriteoffPlan.KeyOf(row, "人民币") == "人民币");
        }

        static List<AutoBatch> Receipts()
        {
            List<AutoBatch> list = new List<AutoBatch>();
            list.Add(Batch(1, "美元", 10m));
            list.Add(Batch(2, "人民币", 5m));
            return list;
        }

        static List<AutoCandidate> Targets()
        {
            List<AutoCandidate> list = new List<AutoCandidate>();
            list.Add(Candidate("T1", "人民币", 3m));
            list.Add(Candidate("T2", "美元", 4m));
            list.Add(Candidate("T3", "美元", 8m));
            return list;
        }

        static void CheckPrepayFlag()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["flag"] = "AR";
            body["partner"] = "C001";
            Expect("auto prepay default", !ArapAutoWriteoffReq.Parse(body).IncludePrepay);
            body["include_prepay"] = true;
            Expect("auto prepay on", ArapAutoWriteoffReq.Parse(body).IncludePrepay);
            body["include_prepay"] = "true";
            Expect("auto prepay bad", Status(body) == 400);
        }

        // 美元 7.1 的一批（收款单冲减行 + 一张发票行）可以取消；同一批里汇率不同 409。
        static void CheckUnwriteoffFx()
        {
            UnwriteoffAsk ask = new UnwriteoffAsk();
            ask.Flag = "AR";
            ask.CancelNo = "HXAR0000000000001";
            List<UnwriteoffRow> rows = new List<UnwriteoffRow>();
            rows.Add(Row("27", "SOZP0000000001", 11, 0, 7.1m, 6m));
            rows.Add(Row("48", "SK0000000001", 0, 21, 7.1m, -6m));
            UnwriteoffPlan plan = ArapUnwriteoffGate.FromRows(ask, rows, "人民币");
            Expect("unwriteoff fx", plan.Targets.Count == 1 && plan.Lines.Count == 1 && plan.Lines[0].Back == 6m);
            rows[1].Rate = 7.2m;
            int status = 0;
            try
            {
                ArapUnwriteoffGate.FromRows(ask, rows, "人民币");
            }
            catch (BridgeException ex)
            {
                status = ex.Status;
            }
            Expect("unwriteoff fx mixed", status == 409);
        }

        static UnwriteoffRow Row(string coType, string coCode, int bvid, int coClose, decimal rate, decimal credit)
        {
            UnwriteoffRow r = new UnwriteoffRow();
            r.Style = "9P";
            r.Flag = "AR";
            r.VType = "48";
            r.VCode = "SK0000000001";
            r.CoType = coType;
            r.CoCode = coCode;
            r.BVid = bvid;
            r.CoClose = coClose;
            r.Period = 9;
            r.Dw = "C001";
            r.Pz = "";
            r.Contract = "";
            r.Cur = "美元";
            r.Rate = rate;
            r.CF = credit;
            return r;
        }

        static int Status(Dictionary<string, object> body)
        {
            try
            {
                ArapAutoWriteoffReq.Parse(body);
                return 200;
            }
            catch (BridgeException ex)
            {
                return ex.Status;
            }
        }

        static WriteoffPlan Plan(string currency, decimal rate, decimal[] amounts)
        {
            WriteoffPlan plan = new WriteoffPlan();
            plan.Flag = "AR";
            plan.Dw = "C001";
            plan.ReceiptCode = "SK0000000001";
            plan.ReceiptDate = "2026-09-01";
            plan.Currency = currency;
            plan.Rate = rate;
            plan.Line = 21;
            plan.Targets = new List<WriteoffTarget>();
            foreach (decimal amount in amounts)
            {
                WriteoffTarget t = new WriteoffTarget();
                t.VType = "27";
                t.Code = "SOZP0000000001";
                t.Date = "2026-09-01";
                t.Line = 11 + plan.Targets.Count;
                t.Amount = amount;
                plan.Targets.Add(t);
                plan.Sum += amount;
            }
            return plan;
        }

        static XmlElement Close(WriteoffPlan plan)
        {
            XmlDocument doc = new XmlDocument();
            doc.LoadXml(WriteoffXml.Build(plan, "2026-09-28"));
            return (XmlElement)doc.DocumentElement.SelectSingleNode("close");
        }

        static string Attr(XmlElement e, string name)
        {
            return e == null ? null : e.GetAttribute(name);
        }

        static AutoBatch Batch(int id, string key, decimal remain)
        {
            AutoBatch b = new AutoBatch();
            b.ReceiptId = id;
            b.Key = key;
            b.Remain = remain;
            return b;
        }

        static AutoCandidate Candidate(string code, string key, decimal balance)
        {
            AutoCandidate t = new AutoCandidate();
            t.Code = code;
            t.Key = key;
            t.Balance = balance;
            t.Left = balance;
            return t;
        }

        static string Describe(List<AutoBatch> plan)
        {
            List<string> parts = new List<string>();
            foreach (AutoBatch b in plan)
            {
                List<string> pieces = new List<string>();
                foreach (AutoPiece p in b.Pieces)
                {
                    pieces.Add(p.Target.Code + "=" + p.Amount.ToString("0.##", System.Globalization.CultureInfo.InvariantCulture));
                }
                parts.Add(b.ReceiptId.ToString(System.Globalization.CultureInfo.InvariantCulture) + ":" + string.Join(",", pieces.ToArray()));
            }
            return string.Join(";", parts.ToArray());
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
