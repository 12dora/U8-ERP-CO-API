using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的生产订单替代料部分（MoSubs、MoUpdateSubs、SubstituteRows、字段权限的替代料别名）：SQL 形状、对外字段、
    // 写回的取舍与核对、遮蔽。只测纯逻辑，不连库。由 MoUpdateSelfTest.Run 调用。
    internal static class MoSubsSelfTest
    {
        public static void Run()
        {
            CheckReadSql();
            CheckInsertSql();
            CheckInsertArgs();
            CheckOutput();
            CheckDates();
            CheckRestore();
            CheckVerify();
            CheckMask();
            MoSubsReplaceSelfTest.Run();
            Expect("mo sub refs", !MoUpdateRefs.Lists("mom_moallocatesub", "AllocateId"));
        }

        static void CheckReadSql()
        {
            string read = MoSubs.ReadSql(2, true);
            Expect("mo sub read lock", read.Contains("from mom_moallocatesub s with (updlock, holdlock) left join bas_part p"));
            Expect("mo sub read tail", read.EndsWith("where s.AllocateId in (?,?) order by s.AllocateId, s.[Sequence], s.PartId",
                StringComparison.Ordinal));
            Expect("mo sub read plain", !MoSubs.ReadSql(1, false).Contains("updlock"));
            Expect("mo sub read date", read.Contains("convert(varchar(23), s.EffBegDate, 121)"));
            Expect("mo sub read float exact", read.Contains("convert(varchar(30), s.Define26, 3)"));
            Expect("mo sub read float shown", read.Contains("convert(varchar(40), s.Define26, 2), '<null>') as Define26_t"));
        }

        static void CheckInsertSql()
        {
            string insert = MoSubs.InsertSql();
            Expect("mo sub insert cols", insert.StartsWith("insert into mom_moallocatesub (MoallocateSubId, AllocateId, [Sequence],"
                + " PartId, Factor, EffBegDate, EffEndDate, ReplaceFlag, Define22", StringComparison.Ordinal));
            Expect("mo sub insert values", insert.Contains(") values (newid(), ?, ?, ?, ?, convert(datetime, ?, 121),"
                + " convert(datetime, ?, 121), ?"));
            Expect("mo sub insert float", insert.Contains("convert(float, ?)"));
            Expect("mo sub insert marks", Count(insert, '?') == 23);
        }

        static void CheckInsertArgs()
        {
            MoSubRow sub = Sub(7, 1, 501, "1.000000", " 文本A ");
            sub.Cells["Define26"] = "3FD3333333333333";
            object[] args = MoSubs.InsertArgs(sub, 900);
            Expect("mo sub args count", args.Length == 23);
            Expect("mo sub args ids", (int)args[0] == 900 && (int)args[1] == 1 && (int)args[2] == 501);
            Expect("mo sub args factor", (string)args[3] == "1.000000");
            Expect("mo sub args null", args[5] == null);
            Expect("mo sub args text kept", (string)args[7] == " 文本A ");
            Expect("mo sub args float hex", (string)args[11] == "3FD3333333333333");
        }

        static void CheckOutput()
        {
            MoSubRow sub = Sub(7, 1, 501, "1.000000", "文本A");
            sub.Cells["Define26"] = "3FF8000000000000";
            sub.Cells["Define26" + MoSubs.ShownSuffix] = "1.500000000000000e+000";
            Dictionary<string, object> item = MoSubs.Output(sub);
            Dictionary<string, object> defines = item["defines"] as Dictionary<string, object>;
            Expect("mo sub out seq", (int)item["sequence"] == 1 && (string)item["inv_code"] == "DEMO-001");
            Expect("mo sub out factor", (decimal)item["factor"] == 1m && !(bool)item["replace_flag"]);
            Expect("mo sub out eff", (string)item["eff_begin"] == "2026-01-01" && item["eff_end"] == null);
            Expect("mo sub out defines", defines != null && defines.Count == 2);
            Expect("mo sub out define text", (string)defines["define22"] == "文本A");
            Expect("mo sub out define float", (double)defines["define26"] == 1.5);
            Expect("mo sub out no defines", !MoSubs.Output(Sub(7, 1, 501, "2", MoUpdateCols.NullMark)).ContainsKey("defines"));
        }

        // 同一套日期规则（物料清单替代料共用）：生效 / 失效日期只到日；日期型自定义项零点只到日，否则到秒。
        static void CheckDates()
        {
            Expect("sub date 121", SubstituteRows.Date("2026-01-01 08:30:00.000") == "2026-01-01");
            Expect("sub date 23", SubstituteRows.Date("2026-01-01") == "2026-01-01");
            Expect("sub date null", SubstituteRows.Date(SubstituteRows.NullMark) == null && SubstituteRows.Date("") == null);
            Expect("sub datetime time", SubstituteRows.DateTimeText("2026-01-01 08:30:00.000") == "2026-01-01 08:30:00");
            Expect("sub datetime 120", SubstituteRows.DateTimeText("2026-01-01 00:00:00") == "2026-01-01");
            Expect("sub define int", (int)SubstituteRows.DefineValue("5", "i") == 5);
        }

        // 保留（没给替代料）：新子件上没有就插快照全部，已有相同的不动，已有不同的不覆盖（null，调用方报 504）。
        static void CheckRestore()
        {
            MoAllocTarget t = new MoAllocTarget();
            t.Row = Old();
            MoSubStep all = MoUpdateSubs.Step(t, new List<MoSubRow>());
            Expect("mo sub insert all", all != null && !all.Clear && all.Add.Count == 1);
            MoSubStep kept = MoUpdateSubs.Step(t, new List<MoSubRow> { Sub(900, 1, 501, "1.000000", MoUpdateCols.NullMark) });
            Expect("mo sub kept", kept != null && !kept.Clear && kept.Add.Count == 0);
            Expect("mo sub kept scale", MoUpdateSubs.Step(t, new List<MoSubRow> { Sub(900, 1, 501, "1.0", MoUpdateCols.NullMark) })
                .Add.Count == 0);
            Expect("mo sub changed", MoUpdateSubs.Step(t, new List<MoSubRow> { Sub(900, 1, 502, "1.000000", MoUpdateCols.NullMark) }) == null);
            Expect("mo sub lost text", MoUpdateRestore.Lost("MO-T1", "x").Message.Contains("mo_update_snapshot"));
        }

        static void CheckVerify()
        {
            MoAllocTarget t = new MoAllocTarget();
            t.Row = Old();
            Dictionary<int, MoAllocTarget> pairs = new Dictionary<int, MoAllocTarget>();
            pairs[900] = t;
            Dictionary<int, List<MoSubRow>> now = new Dictionary<int, List<MoSubRow>>();
            Expect("mo sub verify none", MoUpdateSubs.Mismatch(pairs, now).Length > 0);
            now[900] = new List<MoSubRow> { Sub(900, 1, 501, "1.000000", MoUpdateCols.NullMark) };
            Expect("mo sub verify ok", MoUpdateSubs.Mismatch(pairs, now).Length == 0);
            now[900][0].Cells["Factor"] = "2.000000";
            Expect("mo sub verify factor", MoUpdateSubs.Mismatch(pairs, now).Length > 0);
        }

        // 拒绝子件的 InvCode、Define22：子件与替代料的对应键都置空，系数照给，masked_fields 列出两边的键。
        static void CheckMask()
        {
            PermContext p = new PermContext();
            p.Acc = "998";
            p.Operator = "op1";
            HashSet<string> denied = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            denied.Add("InvCode");
            denied.Add("Define22");
            p.Columns["MO"] = denied;
            Dictionary<string, object> body = MaskBody();
            WorkItem item = new WorkItem();
            item.Path = "/u8co/v1/vouchers/load";
            item.Type = Kinds.Find("production_order");
            item.Body = new Dictionary<string, object>();
            PermMask.Mask(p, item.Path, PermColumnMap.Plan(item), body);
            Dictionary<string, object> alloc = (Dictionary<string, object>)((List<object>)body["allocations"])[0];
            Dictionary<string, object> sub = (Dictionary<string, object>)((List<object>)alloc[MoSubs.Key])[0];
            Dictionary<string, object> defines = (Dictionary<string, object>)sub["defines"];
            Expect("mo sub mask alloc", alloc["InvCode"] == null);
            Expect("mo sub mask inv", sub["inv_code"] == null && defines["define22"] == null);
            Expect("mo sub mask kept", (decimal)sub["factor"] == 1m);
            List<string> masked = body[PermMask.Key] as List<string>;
            Expect("mo sub mask list", masked != null && masked.Contains("inv_code") && masked.Contains("define22"));
        }

        static Dictionary<string, object> MaskBody()
        {
            Dictionary<string, object> sub = MoSubs.Output(Sub(7, 1, 501, "1.000000", "文本A"));
            Dictionary<string, object> alloc = new Dictionary<string, object>();
            alloc["InvCode"] = "DEMO-001";
            alloc[MoSubs.Key] = new List<object> { sub };
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["allocations"] = new List<object> { alloc };
            return body;
        }

        internal static MoAllocRow Old()
        {
            MoAllocRow old = new MoAllocRow();
            old.InvCode = "DEMO-001";
            old.SortSeq = 1;
            old.SubRows = new List<MoSubRow> { Sub(7, 1, 501, "1.000000", MoUpdateCols.NullMark) };
            return old;
        }

        internal static MoSubRow Sub(int allocateId, int seq, int partId, string factor, string define22)
        {
            MoSubRow sub = new MoSubRow();
            sub.AllocateId = allocateId;
            sub.Sequence = seq;
            sub.PartId = partId;
            sub.InvCode = "DEMO-001";
            sub.Cells["Factor"] = factor;
            sub.Cells["ReplaceFlag"] = "0";
            sub.Cells["EffBegDate"] = "2026-01-01 00:00:00.000";
            sub.Cells["Define22"] = define22;
            return sub;
        }

        static int Count(string text, char c)
        {
            int n = 0;
            for (int i = 0; i < text.Length; i++)
            {
                if (text[i] == c)
                {
                    n++;
                }
            }
            return n;
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
