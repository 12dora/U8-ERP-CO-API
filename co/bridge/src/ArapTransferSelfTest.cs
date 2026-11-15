using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的应收冲应付 / 应付冲应收部分：请求校验（收付款单不收、单号不分大小写查重）、号和方向规则、红字判定、
    // 按行分摊、外币折算、处理行 INSERT 的参数顺序、两侧本币相等、行数。只跑纯函数，不连库、不建 COM。
    internal static class ArapTransferSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckRules();
            CheckSpread();
            CheckInsert();
            CheckNativeEqual();
            CheckDigest();
        }

        static void CheckParse()
        {
            Dictionary<string, object> body = Body();
            TransferAsk ask = ArapTransferReq.Parse(body);
            Expect("transfer parse", ask.Flag == "AR" && ask.Customer == "C001" && ask.Vendor == "S001" && ask.Currency == ""
                && ask.Ar.Count == 1 && ask.Ap.Count == 2 && ask.Sum == 100m && ask.Ap[0].Line == 11);
            Expect("transfer locks", string.Join(",", ArapTransferReq.LockKeys(body)) == "arap:writeoff:AP,arap:writeoff:AR");
            ((Dictionary<string, object>)((List<object>)body["ap_lines"])[1])["amount"] = 39.99m;
            Expect("transfer sum", Status(body) == 400);
            body = Body();
            ((Dictionary<string, object>)((List<object>)body["ar_lines"])[0])["line_id"] = 5;
            Expect("transfer bill line", Status(body) == 400);
            body = Body();
            Dictionary<string, object> again = (Dictionary<string, object>)((List<object>)body["ap_lines"])[1];
            again["type"] = "01";
            again["id"] = "CG0001";
            Expect("transfer overlap", Status(body) == 400);
            body = Body();
            ((List<object>)body["ap_lines"]).Add(Line("01", "cg0001", 11, 0.01m));
            ((Dictionary<string, object>)((List<object>)body["ar_lines"])[0])["amount"] = 100.01m;
            Expect("transfer overlap case", Status(body) == 400);
            body = Body();
            ((Dictionary<string, object>)((List<object>)body["ar_lines"])[0])["type"] = "01";
            Expect("transfer side type", Status(body) == 400);
            body = Body();
            ((Dictionary<string, object>)((List<object>)body["ar_lines"])[0])["type"] = "48";
            Expect("transfer receipt", Message(body) == "转账只支持发票和应收单 / 应付单；收付款单请用核销");
            body = Body();
            ((Dictionary<string, object>)((List<object>)body["ap_lines"])[1])["type"] = "49";
            Expect("transfer payment", Status(body) == 400);
            body = Body();
            body["flag"] = "XX";
            Expect("transfer flag", Status(body) == 400);
        }

        static void CheckRules()
        {
            Expect("transfer style", All(ArapTransferRule.Style("AR") == "9I", ArapTransferRule.Style("AP") == "9J"));
            Expect("transfer no", All(ArapTransferRule.NoValid("AR", "YCFAP000000900003"), ArapTransferRule.NoValid("AP", "FCYAR000000000001"),
                !ArapTransferRule.NoValid("AR", "FCYAR000000000001"), !ArapTransferRule.NoValid("AR", "YCFAP00000000000X"),
                !ArapTransferRule.NoValid("AR", null)));
            Expect("transfer ar credit", All(ArapTransferRule.Credit("AR"), !ArapTransferRule.Credit("AP")));
            Expect("transfer mode", All(ArapTransferRule.Mode("P0") == ArapTransferRule.WholeDoc,
                ArapTransferRule.Mode("R0") == ArapTransferRule.WholeDoc, ArapTransferRule.Mode("26") == ArapTransferRule.ByInvoiceLine,
                ArapTransferRule.Mode("02") == ArapTransferRule.ByInvoiceLine));
            Expect("transfer kind", All(ArapTransferRule.KindOf("27") == "sale_invoice", ArapTransferRule.KindOf("01") == "purchase_invoice",
                ArapTransferRule.KindOf("R0") == "ar_bill", ArapTransferRule.KindOf("P0") == "ap_bill",
                ArapTransferRule.SideOf("R0") == "AR", ArapTransferRule.SideOf("26") == "AR", ArapTransferRule.SideOf("P0") == "AP",
                ArapTransferRule.SideOf("02") == "AP"));
            CheckRed();
        }

        // 红字：没有正余额行而有负余额行；有一行正余额、全为 0 或没有行都不算。
        static void CheckRed()
        {
            List<TransferSlot> red = new List<TransferSlot>();
            red.Add(Slot(1, -50m, -50m));
            red.Add(Slot(2, 0m, 0m));
            List<TransferSlot> mixed = new List<TransferSlot>(red);
            mixed.Add(Slot(3, 10m, 10m));
            List<TransferSlot> zero = new List<TransferSlot>();
            zero.Add(Slot(4, 0m, 0m));
            Expect("transfer red", All(ArapTransferRule.Red(red), !ArapTransferRule.Red(mixed), !ArapTransferRule.Red(zero),
                !ArapTransferRule.Red(new List<TransferSlot>())));
            Expect("transfer red spread", ArapTransferRule.Spread(Doc("26", "AR", 0, 10m), red, 10m, true) == null);
        }

        // 三行采购发票：没给 line_id 时按行主键从小到大分摊；余额不够返回 null；外币整行转走取本币余额。
        static void CheckSpread()
        {
            TransferDoc doc = Doc("01", "AP", 0, 100m);
            List<TransferSlot> slots = new List<TransferSlot>();
            slots.Add(Slot(1000000014, 40.00m, 40.00m));
            slots.Add(Slot(1000000012, 80.00m, 80.00m));
            slots.Add(Slot(1000000013, 5.00m, 5.00m));
            List<TransferPiece> pieces = ArapTransferRule.Spread(doc, slots, 100m, true);
            Expect("transfer spread null", pieces != null);
            Expect("transfer spread", All(pieces.Count == 3, pieces[0].Line == 1000000012, pieces[0].F == 80.00m,
                pieces[1].F == 5.00m, pieces[2].Line == 1000000014, pieces[2].F == 15.00m, pieces[2].N == 15.00m));
            Expect("transfer spread short", ArapTransferRule.Spread(doc, slots, 125.01m, true) == null);
            doc.Rate = 7.1m;
            TransferSlot fx = Slot(7, 10m, 71.05m);
            Expect("transfer native", All(ArapTransferRule.Native(10m, fx, false, 7.1m) == 71.05m,
                ArapTransferRule.Native(3.33m, fx, false, 7.1m) == 23.64m, ArapTransferRule.Native(3.33m, fx, true, 7.1m) == 3.33m));
        }

        // 处理行的 INSERT：? 的个数与参数个数一致；应收一侧记贷方、应付一侧记借方；参数顺序 期间、日期、摘要、金额、处理方式、处理号……；
        // iFlag 写 0，isignseq、ino_id 写空（不抄审核行的凭证），应付一侧审核人原样复制审核行；发票按行（iBVid）定位审核行。
        static void CheckInsert()
        {
            TransferPlan plan = Plan();
            TransferPiece p = Piece(Doc("R0", "AR", 0, 100m), 0, 100m);
            List<object> args = new List<object>();
            string sql = TransferSql.Insert(plan, p, "C001", args);
            Expect("transfer insert args", All(Count(sql, '?') == args.Count, args.Count == 14));
            Expect("transfer insert head", All(sql.StartsWith("insert into Ar_Detail (iPeriod, cVouchType", StringComparison.Ordinal),
                sql.Contains("s.cCode, s.cItem_Class"), sql.Contains("and isnull(s.iBVid,0)=0 order by s.Auto_ID")));
            Expect("transfer insert values", All((int)args[0] == 9, (string)args[1] == "2026-09-28", (string)args[2] == "应收冲应付 供应商甲",
                (string)args[3] == "100.00", (string)args[4] == "100.00", (string)args[5] == "9I",
                (string)args[6] == "YCFAP000000900003", (string)args[7] == "AR", (string)args[8] == "张三", (string)args[9] == "张三"));
            Expect("transfer insert credit", sql.Contains("s.iExchRate, 0, convert(decimal(28,2), ?), 0, convert(decimal(28,2), ?), 0, 0"));
            Expect("transfer insert voucher cols", All(sql.Contains("s.csign, NULL, NULL, ?"), sql.Contains("NULL, 0, 0, s.cVouchType")));
            TransferPiece inv = Piece(Doc("01", "AP", 31, 100m), 31, 100m);
            args = new List<object>();
            sql = TransferSql.Insert(plan, inv, "S001", args);
            Expect("transfer insert debit", All(Count(sql, '?') == args.Count, args.Count == 14,
                sql.Contains("s.iExchRate, convert(decimal(28,2), ?), 0, convert(decimal(28,2), ?), 0, 0, 0"),
                sql.Contains("and s.iBVid=?"), sql.Contains("?, s.cCheckMan, s.iOrderType"), (string)args[7] == "AP",
                (int)args[args.Count - 1] == 31));
            List<TransferDoc> docs = new List<TransferDoc>();
            docs.Add(inv.Doc);
            docs.Add(p.Doc);
            Expect("transfer rows", ArapTransferWrite.Expected(docs) == 2);
        }

        // 外币：两侧折合本币不等 409。
        static void CheckNativeEqual()
        {
            TransferPlan plan = Plan();
            plan.Home = false;
            plan.Ar.Add(Piece(Doc("26", "AR", 11, 10m), 11, 10m).Doc);
            plan.Ap.Add(Piece(Doc("01", "AP", 21, 10m), 21, 10m).Doc);
            plan.Ap[0].Pieces[0].N = 71.01m;
            plan.Ar[0].Pieces[0].N = 71.00m;
            int status = 0;
            try
            {
                ArapTransferGate.NativeEqual(plan);
            }
            catch (BridgeException ex)
            {
                status = ex.Status;
            }
            Expect("transfer native equal", status == 409);
            plan.Ar[0].Pieces[0].N = 71.01m;
            ArapTransferGate.NativeEqual(plan);
        }

        static void CheckDigest()
        {
            TransferAsk ask = ArapTransferReq.Parse(Body());
            Expect("transfer digest", ArapTransferGate.Digest(ask, "客户甲", "供应商甲") == "应收冲应付 供应商甲");
            ask.Flag = "AP";
            Expect("transfer digest ap", ArapTransferGate.Digest(ask, "", "供应商甲") == "应付冲应收 C001");
            ask.Digest = "抵账";
            Expect("transfer digest given", ArapTransferGate.Digest(ask, "客户甲", "供应商甲") == "抵账");
        }

        static Dictionary<string, object> Body()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["flag"] = "AR";
            body["customer"] = "C001";
            body["vendor"] = "S001";
            List<object> ar = new List<object>();
            ar.Add(Line("R0", "YS0001", 0, 100m));
            List<object> ap = new List<object>();
            ap.Add(Line("01", "CG0001", 11, 60m));
            ap.Add(Line("P0", "YF0001", 0, 40m));
            body["ar_lines"] = ar;
            body["ap_lines"] = ap;
            return body;
        }

        static Dictionary<string, object> Line(string type, string id, int line, decimal amount)
        {
            Dictionary<string, object> m = new Dictionary<string, object>();
            m["type"] = type;
            m["id"] = id;
            if (line > 0)
            {
                m["line_id"] = line;
            }
            m["amount"] = amount;
            return m;
        }

        static int Status(Dictionary<string, object> body)
        {
            try
            {
                ArapTransferReq.Parse(body);
                return 200;
            }
            catch (BridgeException ex)
            {
                return ex.Status;
            }
        }

        static string Message(Dictionary<string, object> body)
        {
            try
            {
                ArapTransferReq.Parse(body);
                return "";
            }
            catch (BridgeException ex)
            {
                return ex.Message;
            }
        }

        static TransferPlan Plan()
        {
            TransferPlan plan = new TransferPlan();
            plan.Flag = "AR";
            plan.Date = "2026-09-28";
            plan.Year = 2026;
            plan.Period = 9;
            plan.Customer = "C001";
            plan.Vendor = "S001";
            plan.Currency = "人民币";
            plan.Local = "人民币";
            plan.Home = true;
            plan.Digest = "应收冲应付 供应商甲";
            plan.Operator = "张三";
            plan.CancelNo = "YCFAP000000900003";
            return plan;
        }

        static TransferDoc Doc(string type, string side, int line, decimal amount)
        {
            TransferAskLine ask = new TransferAskLine();
            ask.Type = type;
            ask.Code = "D0001";
            ask.Line = line;
            ask.Amount = amount;
            TransferDoc d = new TransferDoc();
            d.Ask = ask;
            d.Side = side;
            d.Kind = ArapTransferRule.KindOf(type);
            d.Title = "单据";
            d.Rate = 1m;
            return d;
        }

        static TransferPiece Piece(TransferDoc d, int line, decimal amount)
        {
            TransferPiece p = new TransferPiece();
            p.Doc = d;
            p.Line = line;
            p.F = amount;
            p.N = amount;
            p.BeforeF = amount;
            d.Pieces.Add(p);
            return p;
        }

        static TransferSlot Slot(int line, decimal f, decimal n)
        {
            TransferSlot s = new TransferSlot();
            s.Line = line;
            s.RemainF = f;
            s.RemainN = n;
            return s;
        }

        static int Count(string text, char c)
        {
            int n = 0;
            foreach (char x in text)
            {
                if (x == c)
                {
                    n++;
                }
            }
            return n;
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
