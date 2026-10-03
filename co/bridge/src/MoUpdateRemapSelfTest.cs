using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的生产订单修改（已审核 / 已领料）部分：行状态闸门、改后需求不少于已领（含超额领料）、旧→新子件对照、引用核对、
    // 并发领料的出库行、拒绝引用的名单、504 合并与提示、回读的审核状态。只测纯逻辑，不连库、不建 COM。由 MoUpdateSelfTest.Run 调用。
    internal static class MoUpdateRemapSelfTest
    {
        public static void Run()
        {
            CheckLineGate();
            CheckIssued();
            CheckMap();
            CheckPoint();
            CheckIssuedAfter();
            CheckLate();
            CheckRefsList();
            CheckSettle();
            CheckState();
        }

        // 1 / 2 / 3 放行；4 先打开；其他状态、集合、审批流、已报检拒绝。
        static void CheckLineGate()
        {
            Expect("mo upd gate 1", Gate(Line("1")) == "");
            Expect("mo upd gate 2", Gate(Line("2")) == "");
            Expect("mo upd gate 3", Gate(Line("3")) == "");
            Expect("mo upd gate 4", Gate(Line("4")) == "409 state_mismatch 生产订单已关闭，请先打开再修改");
            Expect("mo upd gate 0", Gate(Line("0")).StartsWith("409 state_mismatch", StringComparison.Ordinal));
            MoLineRow cf = Line("3");
            cf.Cf = "1";
            Expect("mo upd gate cf", Gate(cf).StartsWith("409 state_mismatch", StringComparison.Ordinal));
            MoLineRow wf = Line("3");
            wf.Wf = "1";
            Expect("mo upd gate wf", Gate(wf).StartsWith("409 workflow_enabled", StringComparison.Ordinal));
            MoLineRow declared = Line("3");
            declared.Declared = 1m;
            Expect("mo upd gate declared", Gate(declared).StartsWith("409 state_mismatch", StringComparison.Ordinal));
        }

        // 已领 0.14：数量减半后需求 0.07 < 0.14 拒绝；翻倍放行。已超额领料（已领 0.2 > 需求 0.14）：只改备注也 409；
        // 没有改动（不调用 U8）放行。另一子件 C002 已领 1.5：数量改成 1.2 后它的需求按重算值 1.2 < 1.5 拒绝，改成 2 放行。
        static void CheckIssued()
        {
            Expect("mo upd issued down", Issued(Plan("0.5", 0.14m, 0.14m)).StartsWith("409", StringComparison.Ordinal));
            Expect("mo upd issued up", Issued(Plan("2", 0.14m, 0.14m)) == "");
            Expect("mo upd issued equal", Issued(Plan("1", 0.14m, 0.14m)) == "");
            MoSnap snap = Snap(0.2m, 0m);
            MoPlan memo = MoUpdatePlan.Build(MoUpdateReq.Parse(new Dictionary<string, object>(),
                new object[] { Edit("remark", "改") }), snap);
            string over = Issued(memo);
            Expect("mo upd issued memo over", over.StartsWith("409", StringComparison.Ordinal)
                && over.Contains("改后需求 0.14") && over.Contains("超额领料"));
            Expect("mo upd issued noop", Issued(MoUpdatePlan.Build(new MoUpdateAsk(), snap)) == "");
            string other = Issued(Plan("1.2", 0m, 1.5m));
            Expect("mo upd issued other", other.StartsWith("409", StringComparison.Ordinal) && other.Contains("C002")
                && other.Contains("改后需求 1.2"));
            Expect("mo upd issued other up", Issued(Plan("2", 0m, 1.5m)) == "");
        }

        // 旧 101 / 102 → 新 201 / 202（按行、行号、存货）；旧 id 仍在对到自己；对不上、重复都 504。
        static void CheckMap()
        {
            MoSnap snap = Snap(0.14m, 0m);
            List<MoAllocKey> now = new List<MoAllocKey>();
            now.Add(MoAllocKey.Of(201, MoAllocKey.KeyOf(11, 10, "c001"), 0.28m, 0.14m));
            now.Add(MoAllocKey.Of(202, MoAllocKey.KeyOf(11, 20, "C002"), 2m, 0m));
            Dictionary<int, int> map = MoUpdateRemap.Map(snap.Allocs, now, "MO-T1");
            Expect("mo upd map", map.Count == 2 && map[101] == 201 && map[102] == 202);
            List<MoAllocKey> kept = new List<MoAllocKey>();
            kept.Add(MoAllocKey.Of(101, MoAllocKey.KeyOf(11, 10, "C001"), 0.28m, 0.14m));
            kept.Add(MoAllocKey.Of(202, MoAllocKey.KeyOf(11, 20, "C002"), 2m, 0m));
            map = MoUpdateRemap.Map(snap.Allocs, kept, "MO-T1");
            Expect("mo upd map kept", map[101] == 101 && map[102] == 202);
            List<MoAllocKey> twice = new List<MoAllocKey>(now);
            twice.Add(MoAllocKey.Of(203, MoAllocKey.KeyOf(11, 10, "C001"), 0.28m, 0m));
            Expect("mo upd map dup", MapFails(snap.Allocs, twice));
            List<MoAllocKey> lost = new List<MoAllocKey>();
            lost.Add(now[1]);
            Expect("mo upd map lost", MapFails(snap.Allocs, lost));
        }

        // 引用行改写后要指向对照的新子件，新子件存在且存货与出库行相同。
        static void CheckPoint()
        {
            MoRef r = new MoRef();
            r.AutoId = "9001";
            r.OldId = 101;
            Expect("mo upd point ok", MoUpdateRemap.PointProblem(r, Point("201", "C001", "c001 "), 201) == "");
            Expect("mo upd point old", MoUpdateRemap.PointProblem(r, Point("101", "C001", ""), 201).Length > 0);
            Expect("mo upd point orphan", MoUpdateRemap.PointProblem(r, Point("201", "C001", ""), 201).Length > 0);
            Expect("mo upd point inv", MoUpdateRemap.PointProblem(r, Point("201", "C001", "C009"), 201).Length > 0);
            Expect("mo upd point gone", MoUpdateRemap.PointProblem(r, null, 201).Length > 0);
        }

        // 新子件已领量等于旧值；需求少于已领不算改写失败（不回滚改写）；有并发领料的子件已领量不比。
        static void CheckIssuedAfter()
        {
            MoSnap snap = Snap(0.14m, 0m);
            Dictionary<int, int> map = new Dictionary<int, int>();
            map[101] = 201;
            HashSet<int> none = new HashSet<int>();
            List<MoAllocKey> now = new List<MoAllocKey>();
            now.Add(MoAllocKey.Of(201, MoAllocKey.KeyOf(11, 10, "C001"), 0.28m, 0.14m));
            Expect("mo upd iss kept", MoUpdateRemap.IssuedProblem(map, now, snap, none) == "");
            now[0] = MoAllocKey.Of(201, MoAllocKey.KeyOf(11, 10, "C001"), 0.28m, 0m);
            Expect("mo upd iss dropped", MoUpdateRemap.IssuedProblem(map, now, snap, none).Length > 0);
            now[0] = MoAllocKey.Of(201, MoAllocKey.KeyOf(11, 10, "C001"), 0.1m, 0.14m);
            Expect("mo upd iss over kept", MoUpdateRemap.IssuedProblem(map, now, snap, none) == "");
            now[0] = MoAllocKey.Of(201, MoAllocKey.KeyOf(11, 10, "C001"), 0.28m, 0.2m);
            Expect("mo upd iss changed", MoUpdateRemap.IssuedProblem(map, now, snap, none).Length > 0);
            HashSet<int> grown = new HashSet<int>();
            grown.Add(101);
            Expect("mo upd iss grown", MoUpdateRemap.IssuedProblem(map, now, snap, grown) == "");
            now.Clear();
            Expect("mo upd iss gone", MoUpdateRemap.IssuedProblem(map, now, snap, grown).Length > 0);
        }

        // 改写事务里重读到的出库行：抓取过的不重复；新出现的（并发领料）补进来标 Late，计入 Grown；存货不同、子件不在快照 504。
        static void CheckLate()
        {
            MoSnap snap = Snap(0.14m, 0m);
            MoRefs refs = new MoRefs();
            refs.Items.Add(MoUpdateRemap.RefOf(Rd("9001", "101", "C001"), snap));
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            rows.Add(Rd("9001", "101", "C001"));
            rows.Add(Rd("9002", "102", "c002 "));
            MoRefs all = MoUpdateRemapLate.Merge(refs, rows, snap);
            Expect("mo upd late merge", all.Items.Count == 2 && !all.Items[0].Late && all.Items[1].Late
                && all.Items[1].OldId == 102 && all.Items[1].Alloc != null);
            HashSet<int> grown = MoUpdateRemapLate.Grown(all);
            Expect("mo upd late grown", grown.Count == 1 && grown.Contains(102) && MoUpdateRemapLate.LateIds(all).Count == 1);
            Expect("mo upd late none", MoUpdateRemapLate.Merge(refs, new List<Dictionary<string, object>>(), snap).Items.Count == 1);
            rows.Add(Rd("9003", "102", "C009"));
            Expect("mo upd late subst", LateFails(refs, rows, snap));
            rows.RemoveAt(2);
            rows.Add(Rd("9004", "999", "C001"));
            Expect("mo upd late unknown", LateFails(refs, rows, snap));
            Expect("mo upd late sql", MoUpdateRemapLate.ReadSql(3).EndsWith("where r.iMPoIds in (?,?,?)", StringComparison.Ordinal)
                && MoUpdateRemapLate.ReadSql(3).Contains("with (updlock, holdlock)"));
        }

        // 材料出库的序列号明细按 AllocateId 引用子件，桥改不准：在拒绝名单里。rdrecords11 由桥改写，不在名单里。
        static void CheckRefsList()
        {
            Expect("mo upd refs sn", MoUpdateRefs.Lists("ST_SNDetail_MaOut", "impoids"));
            Expect("mo upd refs rd11", !MoUpdateRefs.Lists("rdrecords11", "iMPoIds"));
        }

        // 改写、写回的 504：只有一个原样；两个都有合并。改写失败的提示指向三条审计事件。
        static void CheckSettle()
        {
            BridgeException remap = MoUpdateRemap.Lost("MO-T1", "测试");
            BridgeException restore = new BridgeException(504, "outcome_unknown", "写回失败");
            Expect("mo upd settle hint", remap.Status == 504 && remap.Message.Contains("mo_update_snapshot")
                && remap.Message.Contains("mo_update_refs") && remap.Message.Contains("mo_update_remap"));
            Expect("mo upd settle none", MoUpdate.Combine(null, null) == null);
            Expect("mo upd settle remap", MoUpdate.Combine(remap, null) == remap);
            Expect("mo upd settle restore", MoUpdate.Combine(null, restore) == restore);
            BridgeException both = MoUpdate.Combine(remap, restore);
            Expect("mo upd settle both", both.Status == 504 && both.Code == "outcome_unknown"
                && both.Message.Contains("mo_update_remap") && both.Message.Contains("写回失败"));
        }

        // 回读的审核状态：Status 3 且有审核人才算已审核（以前写死 false）。
        static void CheckState()
        {
            MoSnap snap = Snap(0m, 0m);
            snap.Lines[0].Status = "3";
            snap.Lines[0].Verifier = "op001";
            snap.Lines[0].VerifiedAt = "2026-10-03";
            Dictionary<string, object> state = MoUpdateCheck.State(snap);
            Expect("mo upd state verified", (bool)state["verified"] && (string)state["verifier"] == "op001"
                && (string)state["verified_at"] == "2026-10-03");
            snap.Lines[0].Status = "2";
            snap.Lines[0].Verifier = "";
            state = MoUpdateCheck.State(snap);
            Expect("mo upd state open", !(bool)state["verified"] && (string)state["verifier"] == "");
        }

        static string Gate(MoLineRow line)
        {
            try
            {
                MoUpdateSql.LineGate(line);
                return "";
            }
            catch (BridgeException ex)
            {
                return ex.Status.ToString(System.Globalization.CultureInfo.InvariantCulture) + " " + ex.Code + " " + ex.Message;
            }
        }

        static string Issued(MoPlan plan)
        {
            try
            {
                MoUpdateRemap.CheckIssued(plan);
                return "";
            }
            catch (BridgeException ex)
            {
                return ex.Status.ToString(System.Globalization.CultureInfo.InvariantCulture) + " " + ex.Message;
            }
        }

        static bool LateFails(MoRefs refs, List<Dictionary<string, object>> rows, MoSnap snap)
        {
            try
            {
                MoUpdateRemapLate.Merge(refs, rows, snap);
            }
            catch (BridgeException ex)
            {
                return ex.Status == 504;
            }
            return false;
        }

        static Dictionary<string, object> Rd(string autoId, string allocateId, string inv)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["AutoID"] = autoId;
            row["ID"] = "800";
            row["AllocateId"] = allocateId;
            row["cInvCode"] = inv;
            row["Qty"] = "0.01";
            return row;
        }

        static bool MapFails(List<MoAllocRow> old, List<MoAllocKey> now)
        {
            try
            {
                MoUpdateRemap.Map(old, now, "MO-T1");
            }
            catch (BridgeException ex)
            {
                return ex.Status == 504;
            }
            return false;
        }

        // 一行订单（MoDId 11，数量 1，两位小数）：子件 C001（行号 10，变动用量 0.14 / 1，已领 iss）、C002（行号 20，1 / 1，已领 iss2）。
        static MoSnap Snap(decimal iss, decimal iss2)
        {
            MoSnap snap = new MoSnap();
            snap.Code = "MO-T1";
            snap.QtyDigits = 2;
            snap.AuxDigits = 2;
            MoLineRow line = Line("3");
            snap.Lines.Add(line);
            snap.Allocs.Add(Alloc(101, 10, "C001", 0.14m, iss));
            snap.Allocs.Add(Alloc(102, 20, "C002", 1m, iss2));
            return snap;
        }

        static MoPlan Plan(string qty, decimal iss, decimal iss2)
        {
            object value = decimal.Parse(qty, System.Globalization.CultureInfo.InvariantCulture);
            return MoUpdatePlan.Build(MoUpdateReq.Parse(new Dictionary<string, object>(), new object[] { Edit("qty", value) }),
                Snap(iss, iss2));
        }

        static MoLineRow Line(string status)
        {
            MoLineRow line = new MoLineRow();
            line.MoDId = 11;
            line.SortSeq = 1;
            line.InvCode = "P001";
            line.Qty = 1m;
            line.MrpQty = 1m;
            line.MoClass = "1";
            line.AuxUnit = "";
            line.Status = status;
            line.Wf = "0";
            line.Cf = "0";
            line.Remark = "";
            line.Start = "2026-10-01";
            line.Due = "2026-10-03";
            line.Row = new Dictionary<string, object>();
            return line;
        }

        static MoAllocRow Alloc(int id, int seq, string inv, decimal n, decimal iss)
        {
            MoAllocRow a = new MoAllocRow();
            a.AllocateId = id;
            a.MoDId = 11;
            a.SortSeq = seq;
            a.InvCode = inv;
            a.Qty = n;
            a.BaseN = n;
            a.BaseD = 1m;
            a.IssQty = iss;
            a.Wh = "01";
            a.Remark = "";
            a.ProductType = "1";
            return a;
        }

        static Dictionary<string, object> Edit(string key, object value)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["op"] = "update";
            row["line_id"] = 11;
            row[key] = value;
            return row;
        }

        static Dictionary<string, object> Point(string allocateId, string inv, string allocInv)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["AllocateId"] = allocateId;
            row["cInvCode"] = inv;
            row["AllocInv"] = allocInv;
            return row;
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
