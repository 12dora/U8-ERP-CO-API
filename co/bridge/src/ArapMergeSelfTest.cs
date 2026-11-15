using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的并账部分：请求校验、按行分配与比例折算、往来明细的金额列方向、插入语句的参数个数、并账号格式。
    // 只跑纯函数，不连库、不建 COM。
    internal static class ArapMergeSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckRefuse();
            CheckWhole();
            CheckSpread();
            CheckRed();
            CheckColumns();
            CheckInsertArgs();
            CheckNumber();
        }

        static void CheckParse()
        {
            MergeAsk ask = ArapMergeReq.Parse(Body("AR", "C001", "C002", Line("26", "SV001", 7, 100.5m), Line("R0", "YS001", 0, null)));
            Expect("merge parse", ask.Flag == "AR" && ask.From == "C001" && ask.To == "C002" && ask.Lines.Count == 2
                && ask.Lines[0].Line == 7 && ask.Lines[0].Amount == 100.5m && ask.Lines[1].Amount == null
                && ask.Digest == ArapMergeReq.DefaultDigest);
            Expect("merge lock", ArapMergeReq.LockKeys(Body("AP", "S1", "S2", Line("P0", "YF1", 0, null)))[0] == "arap:writeoff:AP");
        }

        static void CheckRefuse()
        {
            Bad("merge same partner", Body("AR", "C001", "c001", Line("26", "SV001", 0, null)), "to");
            Bad("merge wrong side type", Body("AR", "C001", "C002", Line("01", "PV001", 0, null)), "lines.0.type");
            Bad("merge bill line", Body("AP", "S1", "S2", Line("P0", "YF1", 3, null)), "lines.0.line_id");
            Bad("merge amount scale", Body("AR", "C1", "C2", Line("26", "SV1", 0, 1.234m)), "lines.0.amount");
            Bad("merge duplicate", Body("AR", "C1", "C2", Line("26", "SV1", 2, null), Line("26", "sv1", 2, 1m)), "lines.1");
            Bad("merge whole and line", Body("AR", "C1", "C2", Line("26", "SV1", 0, null), Line("26", "SV1", 2, 1m)), "lines.1");
            Bad("merge no lines", Body("AR", "C1", "C2"), "lines");
        }

        // 省略 amount：各行全部并走，本币、数量取余额原值（外币不按比例重算）。
        static void CheckWhole()
        {
            List<MergeLine> open = Open(Bal(1, 100m, 690.12m, 2m), Bal(2, 50m, 345.06m, 1m));
            List<MergeLine> used = ArapMergePlan.Allocate(Ask(0, null), open, "销售发票 SV1");
            Expect("merge whole", used.Count == 2 && used[0].MoveF == 100m && used[0].MoveN == 690.12m && used[0].MoveS == 2m
                && used[1].MoveF == 50m && used[1].MoveN == 345.06m);
        }

        // 给了 amount 没给 line_id：按行号依次分配，部分行按比例折本币（2 位）、数量（6 位）；超过合计 409。
        static void CheckSpread()
        {
            List<MergeLine> used = ArapMergePlan.Allocate(Ask(0, 120m), Open(Bal(1, 100m, 700m, 4m), Bal(2, 50m, 350m, 3m)), "x");
            Expect("merge spread", used.Count == 2 && used[0].MoveF == 100m && used[1].MoveF == 20m
                && used[1].MoveN == 140m && used[1].MoveS == 1.2m);
            used = ArapMergePlan.Allocate(Ask(2, 10m), Open(Bal(1, 100m, 700m, 0m), Bal(2, 30m, 100m, 0m)), "x");
            Expect("merge one line", used.Count == 1 && used[0].Line == 2 && used[0].MoveN == 33.33m);
            Refused("merge over", Ask(0, 151m), Open(Bal(1, 100m, 100m, 0m), Bal(2, 50m, 50m, 0m)));
            Refused("merge line missing", Ask(9, null), Open(Bal(1, 100m, 100m, 0m)));
            Refused("merge empty", Ask(0, null), new List<MergeLine>());
        }

        // 红字行（余额为负）：并走数随余额为负；方向不一致的单据不能不指定行做部分并账。
        static void CheckRed()
        {
            List<MergeLine> used = ArapMergePlan.Allocate(Ask(0, 5m), Open(Bal(1, -20m, -20m, -1m)), "x");
            Expect("merge red", used[0].MoveF == -5m && used[0].MoveN == -5m && used[0].MoveS == -0.25m);
            Refused("merge mixed", Ask(0, 5m), Open(Bal(1, 30m, 30m, 0m), Bal(2, -10m, -10m, 0m)));
            used = ArapMergePlan.Allocate(Ask(0, null), Open(Bal(1, 30m, 30m, 0m), Bal(2, -10m, -10m, 0m)), "x");
            Expect("merge mixed whole", used.Count == 2 && used[1].MoveF == -10m);
        }

        // 应收记借方、应付记贷方；出方为负、入方为正（如 R0 出方 iDAmount=-100、入方 +100）。
        static void CheckColumns()
        {
            MergeLine line = Bal(0, 100m, 100m, 0m);
            ArapMergePlan.Move(line, 100m);
            decimal[] ar = ArapMergePlan.Amounts("AR", line, false);
            decimal[] ap = ArapMergePlan.Amounts("AP", line, true);
            Expect("merge ar source", ar[0] == -100m && ar[1] == 0m && ar[2] == -100m && ar[3] == 0m);
            Expect("merge ap target", ap[0] == 0m && ap[1] == 100m && ap[2] == 0m && ap[3] == 100m);
        }

        static void CheckInsertArgs()
        {
            MergePlan plan = new MergePlan();
            plan.Flag = "AP";
            plan.From = "S1";
            plan.To = "S2";
            plan.Date = "2026-09-30";
            plan.Period = 9;
            plan.Digest = "并账";
            plan.CancelNo = "BZAP0000000000001";
            plan.User = "张三";
            MergeLine line = Bal(1000000012, 80m, 80m, 0m);
            line.Type = "01";
            line.Code = "PV0001";
            ArapMergePlan.Move(line, 0.1m);
            object[] args = ArapMergeSql.InsertArgs(plan, line, true);
            Expect("merge insert marks", args.Length == ArapMergeSql.Marks("AP") && args.Length == ArapMergeSql.Marks("AR"));
            Expect("merge insert args", (int)args[0] == 9 && (string)args[2] == "S2" && (string)args[5] == "0.1"
                && (string)args[4] == "0" && args.Length == 18 && (string)args[12] == "张三" && (int)args[16] == 1000000012
                && (string)args[17] == "S1");
            string sql = ArapMergeSql.InsertQuery("AP");
            Expect("merge insert copies", sql.Contains("s.csign, NULL, NULL, ?") && sql.Contains("?, s.cCheckMan, s.iOrderType"));
            Expect("merge open sql", ArapMergeSql.OpenQuery("AP").Contains("isnull(d.iCAmount_f,0)-isnull(d.iDAmount_f,0)"));
        }

        static void CheckNumber()
        {
            Expect("merge number", ArapMergeSql.IsNumber("BZAR0000000000001", "AR") && !ArapMergeSql.IsNumber("BZAR0000000000001", "AP")
                && !ArapMergeSql.IsNumber("HXAR0000000000002", "AR") && !ArapMergeSql.IsNumber("", "AR"));
        }

        static Dictionary<string, object> Body(string flag, string from, string to, params Dictionary<string, object>[] lines)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["flag"] = flag;
            body["from"] = from;
            body["to"] = to;
            body["lines"] = new List<object>(lines);
            return body;
        }

        static Dictionary<string, object> Line(string type, string id, int line, decimal? amount)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["type"] = type;
            map["id"] = id;
            if (line > 0)
            {
                map["line_id"] = line;
            }
            if (amount != null)
            {
                map["amount"] = amount.Value;
            }
            return map;
        }

        static MergeAskLine Ask(int line, decimal? amount)
        {
            MergeAskLine ask = new MergeAskLine();
            ask.Type = "26";
            ask.Code = "SV1";
            ask.Line = line;
            ask.Amount = amount;
            return ask;
        }

        static MergeLine Bal(int line, decimal f, decimal n, decimal s)
        {
            MergeLine m = new MergeLine();
            m.Line = line;
            m.BalF = f;
            m.BalN = n;
            m.BalS = s;
            return m;
        }

        static List<MergeLine> Open(params MergeLine[] lines)
        {
            return new List<MergeLine>(lines);
        }

        static void Bad(string name, Dictionary<string, object> body, string field)
        {
            try
            {
                ArapMergeReq.Parse(body);
            }
            catch (BridgeException ex)
            {
                Expect(name + " status", ex.Status == 400);
                Expect(name + " field " + ex.Field, ex.Field == field);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void Refused(string name, MergeAskLine ask, List<MergeLine> open)
        {
            try
            {
                ArapMergePlan.Allocate(ask, open, "x");
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 409);
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
