using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的取消票据处理部分（arap/process/cancel 的 PJJ / PJT / PJB / CL）：请求校验、锁键、处理行和往来明细的闸门、
    // 票据余额核对、分包可用区间的合并、SQL 占位符个数。只跑纯函数，不连库、不建 COM。样例为虚构数据（形状同 U8 的票据处理记录：
    // 贴现 100000、分包票据背书 80 后余额 3920 区间 10608001–11000000、退回生成 R0 应收单）。
    internal static class NotesUndoSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckKeys();
            CheckSub();
            CheckRows();
            CheckHead();
            CheckMerge();
            CheckRanges();
            CheckSql();
        }

        static void CheckParse()
        {
            ProcCancelAsk ask = ArapProcCancelReq.Parse(Body("AR", "PJTAR000000000001"));
            Expect("note parse 9D", ask.Kind.Notes && ask.Kind.Style == "9D" && ask.Kind.Ledgers.Length == 1 && ask.Kind.Ledgers[0] == "AR");
            ask = ArapProcCancelReq.Parse(Body("AR", "PJBAR000000000001"));
            Expect("note parse 9E", ask.Kind.Notes && ask.Kind.Style == "9E" && ask.Kind.Ledgers[1] == "AP" && ask.Kind.Title == "票据背书");
            ask = ArapProcCancelReq.Parse(Body("AR", "CLAR0000000000001"));
            Expect("note parse 9C", ask.Kind.Style == "9C" && ask.Kind.Name == "note_return");
            foreach (string ap in new string[] { "PJTAP000000000001", "PJBAP000000000001" })
            {
                Status("note parse ap " + ap, 400, delegate { ArapProcCancelReq.Parse(Body("AP", ap)); });
                Status("note parse ap ar " + ap, 400, delegate { ArapProcCancelReq.Parse(Body("AR", ap)); });
            }
            Expect("note keys ap", ArapProcCancelReq.LockKeys(Body("AP", "PJBAP000000000001")).Length == 0);
            CheckParseAp();
            Expect("note old kinds", !ArapProcCancelReq.Parse(Body("AR", "YCFAP000000000001")).Kind.Notes);
            Status("note parse flag", 400, delegate { ArapProcCancelReq.Parse(Body("AP", "PJJAR000000000001")); });
            Status("note parse prefix", 400, delegate { ArapProcCancelReq.Parse(Body("AR", "PJZAR000000000003")); });
        }

        // 应付票据的结算、退回放行（第二级写入，测试账套名单在登录前另查）。
        static void CheckParseAp()
        {
            ProcCancelAsk ask = ArapProcCancelReq.Parse(Body("AP", "CLAP0000000000001"));
            Expect("note parse ap 9C", ask.Kind.Notes && ask.Kind.Style == "9C" && ask.Kind.Ledgers.Length == 1 && ask.Kind.Ledgers[0] == "AP");
            ask = ArapProcCancelReq.Parse(Body("AP", "PJJAP000000000001"));
            Expect("note parse ap 9A", ask.Kind.Notes && ask.Kind.Style == "9A" && ask.Kind.Flag == "AP");
            Status("note parse ap 9C ar", 400, delegate { ArapProcCancelReq.Parse(Body("AR", "CLAP0000000000001")); });
            Expect("note keys ap 9C", string.Join(",", ArapProcCancelReq.LockKeys(Body("AP", "CLAP0000000000001")))
                == "arap:writeoff:AP,arap:proc:CLAP0000000000001");
        }

        static void CheckKeys()
        {
            string keys = string.Join(",", ArapProcCancelReq.LockKeys(Body("AR", "PJBAR000000000001")));
            Expect("note keys 9E", keys == "arap:writeoff:AR,arap:writeoff:AP,arap:proc:PJBAR000000000001");
            keys = string.Join(",", ArapProcCancelReq.LockKeys(Body("AR", "PJJAR000000000001")));
            Expect("note keys 9A", keys == "arap:writeoff:AR,arap:proc:PJJAR000000000001");
            ProcCancelKind side = NotesUndoGate.SideKind(ArapProcCancelReq.KindOf("PJBAR1"), "AP");
            Expect("note side kind", side.Style == "9E" && side.Ledgers.Length == 1 && side.Ledgers[0] == "AP" && side.Restore && !side.Notes);
        }

        // 一个处理号一条处理行；没有 404，多条、flag 不符、已制单 409。
        static void CheckSub()
        {
            NoteUndoPlan plan = Plan("PJTAR000000000001");
            List<NoteUndoSub> subs = new List<NoteUndoSub>();
            Status("note sub none", 404, delegate { NotesUndoGate.OneSub(subs, plan); });
            subs.Add(Sub("AR", ""));
            Expect("note sub one", NotesUndoGate.OneSub(subs, plan) == subs[0]);
            subs[0].Pz = "AR0000000000001";
            Status("note sub pz", 409, delegate { NotesUndoGate.OneSub(subs, plan); });
            subs[0].Pz = "";
            subs[0].Flag = "AP";
            Status("note sub flag", 409, delegate { NotesUndoGate.OneSub(subs, plan); });
            subs[0].Flag = "AR";
            subs.Add(Sub("AR", ""));
            Status("note sub two", 409, delegate { NotesUndoGate.OneSub(subs, plan); });
        }

        // 贴现：只有一行票据行；退回：票据行 + 生成的 R0 行；多出的行、已制单、期间不一致 409。
        static void CheckRows()
        {
            NoteUndoPlan plan = Plan("PJTAR000000000001");
            plan.Sub = Sub("AR", "");
            plan.Rows.Add(Row("9D", "50", "N2-2-50000001-60000000", 0m, 100000m));
            NotesUndoGate.CheckRows(plan);
            plan.Rows.Add(Row("9D", "R0", "YS1", 1m, 0m));
            Status("note rows extra", 409, delegate { NotesUndoGate.CheckRows(plan); });
            plan.Rows.RemoveAt(1);
            plan.Rows[0].Pz = "AR0000000000001";
            Status("note rows pz", 409, delegate { NotesUndoGate.CheckRows(plan); });
            NoteUndoPlan back = Plan("CLAR0000000000001");
            back.Sub = Sub("AR", "");
            back.Sub.CoType = "R0";
            back.Sub.CoId = "YS0000000002";
            back.Rows.Add(Row("9C", "R0", "YS0000000002", 100000m, 0m));
            back.Rows.Add(Row("9C", "50", "N1-1-10000000", 0m, 100000m));
            NotesUndoGate.CheckRows(back);
            back.Rows[1].Period = 7;
            Status("note rows period", 409, delegate { NotesUndoGate.CheckRows(back); });
            back.Rows[1].Period = 6;
            back.Rows.RemoveAt(0);
            Status("note rows no bill", 409, delegate { NotesUndoGate.CheckRows(back); });
        }

        // 加回后余额不超过票面；加回为 0、票据不存在 409。
        static void CheckHead()
        {
            NoteUndoPlan plan = Plan("PJBAR000000000001");
            plan.Head = Head(10000m, 3920m);
            plan.Back = 80m;
            NotesUndoGate.CheckHead(plan);
            plan.Back = 7000m;
            Status("note head over", 409, delegate { NotesUndoGate.CheckHead(plan); });
            plan.Back = 0m;
            Status("note head zero", 409, delegate { NotesUndoGate.CheckHead(plan); });
            plan.Head = null;
            Status("note head missing", 409, delegate { NotesUndoGate.CheckHead(plan); });
        }

        static void CheckMerge()
        {
            List<NoteAvailRange> left = new List<NoteAvailRange>();
            left.Add(Range(10608001, 11000000, 3920m));
            List<NoteAvailRange> merged = NotesUndoGate.Merge(left, Range(10600001, 10608000, 80m));
            Expect("note merge join", merged.Count == 1 && merged[0].Start == 10600001 && merged[0].End == 11000000
                && merged[0].Amount == 4000m && merged[0].Local == 4000m && left[0].Start == 10608001);
            merged = NotesUndoGate.Merge(left, Range(1, 100, 1m));
            Expect("note merge gap", merged.Count == 2 && merged[0].Start == 1 && merged[1].Start == 10608001);
            Expect("note merge overlap", NotesUndoGate.Merge(left, Range(10608000, 10608001, 0.02m)) == null);
            merged = NotesUndoGate.Merge(new List<NoteAvailRange>(), Range(1, 50000000, 500000m));
            Expect("note merge empty", merged.Count == 1 && merged[0].Amount == 500000m);
        }

        // 分包：处理行要有子票区间且在票据区间内；现有区间合计要等于余额。
        static void CheckRanges()
        {
            NoteUndoPlan plan = Plan("PJBAR000000000001");
            plan.Head = Head(10000m, 3920m);
            plan.Head.Split = true;
            plan.Head.Start = 10000001;
            plan.Head.End = 11000000;
            plan.Sub = Sub("AR", "");
            plan.Sub.Ranged = true;
            plan.Sub.Start = 10600001;
            plan.Sub.End = 10608000;
            plan.Back = 80m;
            plan.BackLocal = 80m;
            List<NoteAvailRange> left = new List<NoteAvailRange>();
            left.Add(Range(10608001, 11000000, 3920m));
            List<NoteAvailRange> merged = NotesUndoGate.Ranges(plan, left);
            Expect("note ranges", merged.Count == 1 && merged[0].Amount == 4000m);
            left[0].Amount = 3900m;
            Status("note ranges sum", 409, delegate { NotesUndoGate.Ranges(plan, left); });
            left[0].Amount = 3920m;
            plan.Sub.End = 11000001;
            Status("note ranges bound", 409, delegate { NotesUndoGate.Ranges(plan, left); });
            plan.Sub.End = 10608000;
            plan.Sub.Ranged = false;
            Status("note ranges none", 409, delegate { NotesUndoGate.Ranges(plan, left); });
        }

        static void CheckSql()
        {
            string[] texts = NotesUndoSql.Texts();
            int[] counts = NotesUndoSql.ArgCounts();
            Expect("note sql count", texts.Length == counts.Length);
            for (int i = 0; i < texts.Length; i++)
            {
                Expect("note sql marks " + i, ProcCancelSql.Marks(texts[i]) == counts[i]);
            }
        }

        static NoteUndoPlan Plan(string no)
        {
            ProcCancelAsk ask = ArapProcCancelReq.Parse(Body(ArapProcCancelReq.KindOf(no).Flag, no));
            NoteUndoPlan plan = new NoteUndoPlan();
            plan.Kind = ask.Kind;
            plan.Flag = ask.Flag;
            plan.CancelNo = ask.CancelNo;
            plan.Ledger = ask.Kind.Ledgers[0];
            return plan;
        }

        static NoteUndoSub Sub(string flag, string pz)
        {
            NoteUndoSub sub = new NoteUndoSub();
            sub.Id = 3887;
            sub.Link = "AR50N1";
            sub.Flag = flag;
            sub.Pz = pz;
            return sub;
        }

        static NoteUndoHead Head(decimal face, decimal remain)
        {
            NoteUndoHead head = new NoteUndoHead();
            head.Id = 3396;
            head.Link = "AR50N1";
            head.Code = "N3";
            head.Flag = "AR";
            head.Face = face;
            head.Remain = remain;
            head.RemainLocal = remain;
            return head;
        }

        static NoteAvailRange Range(long start, long end, decimal amount)
        {
            NoteAvailRange r = new NoteAvailRange();
            r.Start = start;
            r.End = end;
            r.Amount = amount;
            r.Local = amount;
            return r;
        }

        static ProcRow Row(string style, string type, string code, decimal df, decimal cf)
        {
            ProcRow row = new ProcRow();
            row.Ledger = "AR";
            row.Style = style;
            row.Flag = "AR";
            row.VType = type;
            row.VCode = code;
            row.CoType = type;
            row.CoCode = code;
            row.Period = 6;
            row.RegDate = "2025-06-26";
            row.Pz = "";
            row.Contract = "";
            row.BusType = "";
            row.DF = df;
            row.CF = cf;
            row.Head = new Dictionary<string, object>();
            row.Head["cDwCode"] = "C001";
            return row;
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

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }
    }
}
