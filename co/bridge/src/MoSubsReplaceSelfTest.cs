using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的生产订单修改替换 / 清空替代料部分（MoUpdateSubsReq、SubstituteReq、MoUpdateSubs.Step 的给定目标、
    // MoUpdatePlan 的 SubsChanged、回读核对）。只测纯逻辑，不连库。由 MoSubsSelfTest.Run 调用。
    internal static class MoSubsReplaceSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckParseBad();
            CheckRows();
            CheckStep();
            CheckPlan();
            CheckFind();
            CheckTier();
        }

        // 只带 allocations 的行也是修改；defines 按列类型转成写库文本。
        static void CheckParse()
        {
            Dictionary<string, object> defines = new Dictionary<string, object>();
            defines["define22"] = "文本A";
            defines["define26"] = 1.5;
            defines["define36"] = "2026-01-02";
            Dictionary<string, object> sub = SubItem("DEMO-002");
            sub["factor"] = 1.5;
            sub["replace_flag"] = true;
            sub["defines"] = defines;
            MoUpdateAsk ask = MoUpdateReq.Parse(null, new object[] { Line(Alloc(10, "C001", sub)) });
            MoAllocEdit a = ask.Edits[0].Allocs[0];
            Expect("mo subs parse only allocs", ask.HasAllocs && a.SortSeq == 10 && a.InvCode == "C001" && a.Subs.Count == 1);
            BomSub s = a.Subs[0];
            Expect("mo subs parse fields", s.Seq == 1 && s.Factor == 1.5m && s.Replace == 1 && s.EffBeg.Length == 0);
            Expect("mo subs parse defines", s.Defines["22"] == "文本A" && s.Defines["26"] == "1.5"
                && s.Defines["36"] == "2026-01-02 00:00:00.000" && !s.Defines.ContainsKey("23"));
            MoUpdateAsk clear = MoUpdateReq.Parse(null, new object[] { Line(Alloc(10, "C001")) });
            Expect("mo subs parse clear", clear.Edits[0].Allocs[0].Subs.Count == 0);
            Expect("mo subs parse keep", !MoUpdateReq.Parse(null, new object[] { Edit("qty", 2) }).HasAllocs);
            Expect("bom subs no defines", Bad(delegate { SubstituteReq.Parse(new ArrayList { WithDefines() }, "substitutes", false); }));
        }

        static void CheckParseBad()
        {
            Dictionary<string, object> dup = Alloc(10, "C001", SubItem("S1"), SubItem("s1"));
            Expect("mo subs dup sub", BadLine(Line(dup)));
            Expect("mo subs dup alloc", BadLine(Line(Alloc(10, "C001"), Alloc(10, "c001"))));
            Expect("mo subs empty allocs", BadLine(Line()));
            Dictionary<string, object> noSubs = Alloc(10, "C001");
            noSubs.Remove("substitutes");
            Expect("mo subs missing subs", BadLine(Line(noSubs)));
            Expect("mo subs range", BadLine(Line(Alloc(10, "C001", With(SubItem("S1"), "eff_begin", "2026-02-01", "eff_end", "2026-01-01")))));
            Expect("mo subs seq", BadLine(Line(Alloc(10, "C001", With(SubItem("S1"), "sequence", 0, null, null)))));
            Expect("mo subs factor", BadLine(Line(Alloc(10, "C001", With(SubItem("S1"), "factor", 0.0000001, null, null)))));
            Expect("mo subs alloc key", BadLine(Line(With(Alloc(10, "C001"), "allocate_id", 7, null, null))));
            Dictionary<string, object> badDefine = WithDefines();
            ((Dictionary<string, object>)badDefine["defines"])["define34"] = "x";
            Expect("mo subs define int", BadLine(Line(Alloc(10, "C001", badDefine))));
            Dictionary<string, object> unknown = WithDefines();
            ((Dictionary<string, object>)unknown["defines"])["define21"] = "x";
            Expect("mo subs define name", BadLine(Line(Alloc(10, "C001", unknown))));
            Expect("mo subs date min", BadLine(Line(Alloc(10, "C001", With(SubItem("S1"), "eff_begin", "1752-12-31", null, null)))));
            Dictionary<string, object> minDay = With(SubItem("S1"), "eff_begin", "1753-01-01", null, null);
            Expect("mo subs date min ok", !BadLine(Line(Alloc(10, "C001", minDay))));
            Dictionary<string, object> oldDefine = WithDefines();
            ((Dictionary<string, object>)oldDefine["defines"])["define36"] = "1700-01-01 00:00:00";
            Expect("mo subs define date min", BadLine(Line(Alloc(10, "C001", oldDefine))));
            object[] many = new object[51];
            for (int i = 0; i < many.Length; i++)
            {
                many[i] = SubItem("S" + i.ToString(System.Globalization.CultureInfo.InvariantCulture));
            }
            Expect("mo subs max", BadLine(Line(Alloc(10, "C001", many))));
        }

        // 请求行的各列与库里读出的同形：系数 6 位小数、日期 style 121、float 按值比。
        static void CheckRows()
        {
            BomSub sub = new BomSub();
            sub.InvCode = "DEMO-002";
            sub.Factor = 1.5m;
            sub.EffBeg = "2026-01-01";
            sub.Defines = new Dictionary<string, string>();
            sub.Defines["26"] = "1.5";
            List<MoSubRow> rows = MoUpdateSubsReq.Rows(900, new List<BomSub> { sub }, new List<int> { 503 });
            MoSubRow r = rows[0];
            Expect("mo subs rows cells", r.AllocateId == 900 && r.PartId == 503 && r.Cell("Factor") == "1.500000"
                && r.Cell("EffBegDate") == "2026-01-01 00:00:00.000" && r.Cell("EffEndDate") == MoUpdateCols.NullMark
                && r.Cell("ReplaceFlag") == "0" && r.Cell("Define22") == MoUpdateCols.NullMark);
            MoSubRow read = MoSubsSelfTest.Sub(900, 1, 503, "1.500000", MoUpdateCols.NullMark);
            read.InvCode = "DEMO-002";
            read.Cells["EffEndDate"] = MoUpdateCols.NullMark;
            read.Cells["Define26"] = "1.5000000000000000e+000";
            foreach (string col in MoSubs.Names())
            {
                if (!read.Cells.ContainsKey(col))
                {
                    read.Cells[col] = MoUpdateCols.NullMark;
                }
            }
            Expect("mo subs rows same", MoSubs.SameSet(rows, new List<MoSubRow> { read }));
            read.Cells["Define26"] = "1.5000000000000002e+000";
            Expect("mo subs rows float", !MoSubs.SameSet(rows, new List<MoSubRow> { read }));
            Expect("mo subs canon", MoSubs.Canon("2.000000", "n") == "2" && MoSubs.Canon("<null>", "n") == "<null>");
        }

        // 给定目标：仍是快照的先删再插；没有就插；已等于目标不动；被别人改过的 null。清空：空目标。
        static void CheckStep()
        {
            MoAllocTarget t = Given(new List<MoSubRow> { MoSubsSelfTest.Sub(7, 1, 503, "2.000000", MoUpdateCols.NullMark) });
            List<MoSubRow> snapshot = t.Row.SubRows;
            MoSubStep swap = MoUpdateSubs.Step(t, snapshot);
            Expect("mo subs step swap", swap != null && swap.Clear && swap.Add.Count == 1 && swap.Add[0].PartId == 503);
            MoSubStep fresh = MoUpdateSubs.Step(t, new List<MoSubRow>());
            Expect("mo subs step fresh", fresh != null && !fresh.Clear && fresh.Add.Count == 1);
            Expect("mo subs step done", MoUpdateSubs.Step(t, t.Want).Add.Count == 0);
            Expect("mo subs step other", MoUpdateSubs.Step(t, new List<MoSubRow> { MoSubsSelfTest.Sub(7, 1, 504, "1", "x") }) == null);
            MoAllocTarget c = Given(new List<MoSubRow>());
            MoSubStep clear = MoUpdateSubs.Step(c, c.Row.SubRows);
            Expect("mo subs step clear", clear != null && clear.Clear && clear.Add.Count == 0);
            Expect("mo subs step cleared", MoUpdateSubs.Step(c, new List<MoSubRow>()).Add.Count == 0);
            Dictionary<int, MoAllocTarget> pairs = new Dictionary<int, MoAllocTarget>();
            pairs[900] = t;
            Dictionary<int, List<MoSubRow>> now = new Dictionary<int, List<MoSubRow>>();
            now[900] = snapshot;
            Expect("mo subs verify want", MoUpdateSubs.Mismatch(pairs, now).Length > 0);
            now[900] = t.Want;
            Expect("mo subs verify ok", MoUpdateSubs.Mismatch(pairs, now).Length == 0 && MoUpdateSubs.CountOf(t) == 1);
        }

        // 只换替代料：行没有改动（不调用 U8），SubsChanged；给的与现有相同时不算改动；回读核对比替代料内容。
        static void CheckPlan()
        {
            MoSnap snap = MoUpdateSelfTest.Snap();
            snap.Allocs[0].SubRows = new List<MoSubRow> { MoSubsSelfTest.Sub(110, 1, 501, "1.000000", MoUpdateCols.NullMark) };
            snap.Allocs[0].Subs = 1;
            MoUpdateAsk ask = MoUpdateReq.Parse(null, new object[] { Line(Alloc(10, "C001")) });
            ask.Subs[110] = new List<MoSubRow>();
            MoPlan plan = MoUpdatePlan.Build(ask, snap);
            MoAllocTarget t = plan.Allocs[11][0];
            Expect("mo subs plan", !plan.Changed && plan.SubsChanged && t.Given && t.SubsChanged && !plan.Allocs[11][1].Given);
            Expect("mo subs plan pairs", MoUpdateSubsWrite.Pairs(plan).Count == 1 && MoUpdateSubsWrite.Pairs(plan).ContainsKey(110));
            ask.Subs[110] = snap.Allocs[0].SubRows;
            Expect("mo subs plan same", !MoUpdatePlan.Build(ask, snap).SubsChanged);
            ask.Subs[110] = new List<MoSubRow>();
            MoSnap after = MoUpdateSelfTest.Snap();
            after.Allocs[0].SubRows = new List<MoSubRow>();
            Expect("mo subs diff cleared", MoUpdateCheck.Diff(plan, after).Count == 0);
            after.Allocs[0].Subs = 1;
            after.Allocs[0].SubRows = snap.Allocs[0].SubRows;
            Expect("mo subs diff kept old", MoUpdateCheck.Diff(plan, after).Count == 1);
            ApiResult done = MoUpdateCheck.Result(Kinds.Find("production_order"), 1, MoUpdateSelfTest.Snap(), plan);
            Expect("mo subs result", done.Body.ContainsKey("allocations") && (int)done.Body["changed"] == 1);
        }

        static void CheckFind()
        {
            MoSnap snap = MoUpdateSelfTest.Snap();
            MoAllocEdit a = new MoAllocEdit();
            a.SortSeq = 20;
            a.InvCode = "c002";
            Expect("mo subs find", MoUpdateSubsReq.Find(snap, snap.Lines[0], a).AllocateId == 120);
            a.SortSeq = 30;
            Expect("mo subs find none", Conflict(snap, a));
            a.SortSeq = 20;
            snap.Allocs.Add(snap.Allocs[1]);
            Expect("mo subs find twice", Conflict(snap, a));
        }

        // 第二级只拦带 allocations 的请求（含开关关闭）；不带的修改（KEEP）不拦。
        static void CheckTier()
        {
            MoUpdateAsk keep = MoUpdateReq.Parse(null, new object[] { Edit("qty", 2) });
            WorkItem item = new WorkItem();
            item.Config = new BridgeConfig();
            item.Acc = "998";
            MoUpdateSubsReq.RequireTier(item, keep);
            MoUpdateAsk allocs = MoUpdateReq.Parse(null, new object[] { Line(Alloc(10, "C001")) });
            TestAccountGateSelfTest.CheckGate("mo subs tier", MoUpdateSubsReq.TestOnly,
                delegate(WorkItem w) { MoUpdateSubsReq.RequireTier(w, allocs); });
        }

        static bool Conflict(MoSnap snap, MoAllocEdit a)
        {
            try
            {
                MoUpdateSubsReq.Find(snap, snap.Lines[0], a);
            }
            catch (BridgeException ex)
            {
                return ex.Status == 409;
            }
            return false;
        }

        static MoAllocTarget Given(List<MoSubRow> want)
        {
            MoAllocTarget t = new MoAllocTarget();
            t.Row = MoSubsSelfTest.Old();
            t.Given = true;
            t.Want = want;
            return t;
        }

        static Dictionary<string, object> SubItem(string inv)
        {
            Dictionary<string, object> sub = new Dictionary<string, object>();
            sub["inv_code"] = inv;
            return sub;
        }

        static Dictionary<string, object> WithDefines()
        {
            Dictionary<string, object> sub = SubItem("S1");
            sub["defines"] = new Dictionary<string, object>();
            return sub;
        }

        static Dictionary<string, object> With(Dictionary<string, object> map, string k1, object v1, string k2, object v2)
        {
            map[k1] = v1;
            if (k2 != null)
            {
                map[k2] = v2;
            }
            return map;
        }

        static Dictionary<string, object> Alloc(int seq, string inv, params object[] subs)
        {
            Dictionary<string, object> alloc = new Dictionary<string, object>();
            alloc["sort_seq"] = seq;
            alloc["inv_code"] = inv;
            alloc["substitutes"] = new ArrayList(subs);
            return alloc;
        }

        static Dictionary<string, object> Line(params object[] allocs)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["op"] = "update";
            row["line_id"] = 11;
            row["allocations"] = new ArrayList(allocs);
            return row;
        }

        static Dictionary<string, object> Edit(string key, object value)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["op"] = "update";
            row["line_id"] = 11;
            row[key] = value;
            return row;
        }

        static bool BadLine(Dictionary<string, object> line)
        {
            return Bad(delegate { MoUpdateReq.Parse(null, new object[] { line }); });
        }

        static bool Bad(Action act)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                return ex.Status == 400;
            }
            return false;
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
