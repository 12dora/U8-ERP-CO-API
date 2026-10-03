using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的生产订单修改部分：只测纯逻辑（请求校验、子件按新数量重算、回读核对），不连库、不建 COM。
    internal static class MoUpdateSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckScale();
            CheckRefuse();
            CheckDiff();
            CheckFormula();
            CheckGuards();
            MoUpdateRemapSelfTest.Run();
            Expect("mo upd restore sql", MoUpdateCols.RestoreSql() == "update mom_moallocate set OpComponentId=?, VirOpComponentIds=?,"
                + " cSubSysBarCode=?, SoDId=?, UpperMoQty=convert(decimal(28,6), ?) where AllocateId=?");
            Expect("mo upd define name", MoExtNames.Define("define28") == "DDefine_28");
        }

        static void CheckParse()
        {
            MoUpdateAsk ask = MoUpdateReq.Parse(Head("说明"), new object[] { Edit(7, "qty", 2) });
            Expect("mo upd parse", ask.HeadRemark == "说明" && ask.Edits.Count == 1 && ask.Edits[0].HasQty && ask.Edits[0].Qty == 2m);
            ExpectBad("mo upd add", Head(null), new object[] { Row("add", 0, "qty", 1) });
            ExpectBad("mo upd delete", Head(null), new object[] { Row("delete", 7, null, null) });
            ExpectBad("mo upd dup", Head(null), new object[] { Edit(7, "qty", 1), Edit(7, "remark", "x") });
            ExpectBad("mo upd empty remark", Head(null), new object[] { Edit(7, "remark", "") });
            ExpectBad("mo upd unknown", Head(null), new object[] { Edit(7, "wh_code", "01") });
            ExpectBad("mo upd nothing", Head(null), new object[0]);
        }

        // 行 1125 → 2250：子件 115/1000 × 2250 = 258.75（2 位），辅计量换算率 25 → 10.35（4 位）；行件数 2250 / 25 = 90。
        static void CheckScale()
        {
            MoSnap snap = Snap();
            MoPlan plan = MoUpdatePlan.Build(MoUpdateReq.Parse(Head(null), new object[] { Edit(11, "qty", 2250) }), snap);
            MoAllocTarget a = plan.Allocs[11][0];
            MoAllocTarget b = plan.Allocs[11][1];
            Expect("mo upd scaled", plan.Changed && a.Qty == 258.75m && a.AuxQty == 10.35m);
            Expect("mo upd scaled plain", b.Qty == 2250m && b.AuxQty == -1m);
            Expect("mo upd line aux", plan.Lines[0].AuxQty == 90m);
            MoPlan same = MoUpdatePlan.Build(MoUpdateReq.Parse(Head(null), new object[] { Edit(11, "qty", 1125) }), snap);
            Expect("mo upd same", !same.Changed && same.Allocs[11][0].Qty == 129.38m);
            MoPlan half = MoUpdatePlan.Build(MoUpdateReq.Parse(Head(null), new object[] { Edit(11, "qty", 1) }), snap);
            Expect("mo upd round", half.Allocs[11][0].Qty == 0.12m);
        }

        static void CheckRefuse()
        {
            MoSnap snap = Snap();
            ExpectPlanBad("mo upd inv", snap, Edit(11, "inv_code", "OTHER"));
            ExpectPlanBad("mo upd due", snap, Edit(11, "due_date", "2026-09-01"));
            ExpectPlanBad("mo upd line", snap, Edit(99, "qty", 1));
            ExpectPlanBad("mo upd digits", snap, Edit(11, "qty", 1.125));
        }

        static void CheckDiff()
        {
            MoSnap snap = Snap();
            MoPlan plan = MoUpdatePlan.Build(MoUpdateReq.Parse(Head(null), new object[] { Edit(11, "remark", "改") }), snap);
            MoSnap after = Snap();
            after.Lines[0].Remark = "改";
            Expect("mo upd diff ok", MoUpdateCheck.Diff(plan, after).Count == 0);
            after.Allocs.RemoveAt(1);
            Expect("mo upd diff lost", MoUpdateCheck.Diff(plan, after).Count == 1);
            MoSnap wiped = Snap();
            wiped.Lines[0].Remark = "改";
            wiped.Allocs.Clear();
            Expect("mo upd diff wiped", MoUpdateCheck.Diff(plan, wiped).Count == 1);
        }

        // U8 的算法：固定用量不随行数量变；子件损耗 ×(1+c/100)；母件损耗 ÷(1−p/100)，返工订单（MoClass=2）不除。
        static void CheckFormula()
        {
            MoSnap snap = Snap();
            MoAllocRow fixedUse = snap.Allocs[1];
            fixedUse.FVFlag = "2";
            fixedUse.CompScrap = 10m;
            MoPlan plan = MoUpdatePlan.Build(MoUpdateReq.Parse(Head(null), new object[] { Edit(11, "qty", 2250) }), snap);
            Expect("mo upd fixed", plan.Allocs[11][1].Qty == 1.1m);
            snap = Snap();
            snap.Allocs[1].ParentScrap = 20m;
            plan = MoUpdatePlan.Build(MoUpdateReq.Parse(Head(null), new object[] { Edit(11, "qty", 2000) }), snap);
            Expect("mo upd parent scrap", plan.Allocs[11][1].Qty == 2500m);
            snap.Lines[0].MoClass = "2";
            plan = MoUpdatePlan.Build(MoUpdateReq.Parse(Head(null), new object[] { Edit(11, "qty", 2000) }), snap);
            Expect("mo upd rework", plan.Allocs[11][1].Qty == 2000m);
            snap = Snap();
            snap.Allocs[0].AuxBaseN = 5m;
            bool refused = false;
            try
            {
                MoUpdatePlan.Build(MoUpdateReq.Parse(Head(null), new object[] { Edit(11, "qty", 2250) }), snap);
            }
            catch (BridgeException ex)
            {
                refused = ex.Status == 409;
            }
            Expect("mo upd aux mismatch", refused);
        }

        // 保不住的子件设置调用前就拒绝；开工日期不开放；有产出品子件的行不能改完工日期。
        static void CheckGuards()
        {
            Dictionary<string, string> keep = new Dictionary<string, string>();
            keep["ParentScrap"] = "5.000";
            keep["QcFlag"] = "0";
            keep["SoCode"] = MoUpdateCols.NullMark;
            keep["StartDemDate"] = "2026-09-28 00:00:00.000";
            keep["EndDemDate"] = "2026-09-29 00:00:00.000";
            List<string> bad = MoUpdateCols.NotDefault(keep);
            Expect("mo upd guard", bad.Count == 2 && bad.Contains("ParentScrap") && bad.Contains("EndDemDate"));
            ExpectBad("mo upd start", Head(null), new object[] { Edit(11, "start_date", "2026-10-01") });
            MoSnap snap = Snap();
            snap.Allocs[1].Keep["ByproductFlag"] = "1";
            bool refused = false;
            try
            {
                MoUpdatePlan.Build(MoUpdateReq.Parse(Head(null), new object[] { Edit(11, "due_date", "2026-10-05") }), snap);
            }
            catch (BridgeException ex)
            {
                refused = ex.Status == 409;
            }
            Expect("mo upd byproduct due", refused);
        }

        static MoSnap Snap()
        {
            MoSnap snap = new MoSnap();
            snap.Code = "MO-T1";
            snap.QtyDigits = 2;
            snap.AuxDigits = 4;
            MoLineRow line = new MoLineRow();
            line.MoDId = 11;
            line.SortSeq = 1;
            line.InvCode = "P001";
            line.Qty = 1125m;
            line.MrpQty = 1125m;
            line.MoClass = "1";
            line.AuxUnit = "02";
            line.ChangeRate = 25m;
            line.Status = "1";
            line.Remark = "";
            line.Start = "2026-09-28";
            line.Due = "2026-09-30";
            line.Row = new Dictionary<string, object>();
            snap.Lines.Add(line);
            MoAllocRow aux = Alloc(10, "C001", 129.38m, 115m, 1000m);
            aux.AuxUnit = "0202";
            aux.ChangeRate = 25m;
            aux.AuxBaseN = 4.6m;
            snap.Allocs.Add(aux);
            snap.Allocs.Add(Alloc(20, "C002", 1125m, 1m, 1m));
            return snap;
        }

        static MoAllocRow Alloc(int seq, string inv, decimal qty, decimal n, decimal d)
        {
            MoAllocRow a = new MoAllocRow();
            a.AllocateId = 100 + seq;
            a.MoDId = 11;
            a.SortSeq = seq;
            a.InvCode = inv;
            a.Qty = qty;
            a.BaseN = n;
            a.BaseD = d;
            a.Wh = "01";
            a.AuxUnit = "";
            a.Remark = "";
            a.ProductType = "1";
            return a;
        }

        static Dictionary<string, object> Head(string remark)
        {
            Dictionary<string, object> head = new Dictionary<string, object>();
            if (remark != null)
            {
                head["remark"] = remark;
            }
            return head;
        }

        static Dictionary<string, object> Edit(int lineId, string key, object value)
        {
            return Row("update", lineId, key, value);
        }

        static Dictionary<string, object> Row(string op, int lineId, string key, object value)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["op"] = op;
            if (lineId > 0)
            {
                row["line_id"] = lineId;
            }
            if (key != null)
            {
                row[key] = value;
            }
            return row;
        }

        static void ExpectBad(string name, Dictionary<string, object> head, object[] lines)
        {
            try
            {
                MoUpdateReq.Parse(head, lines);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void ExpectPlanBad(string name, MoSnap snap, Dictionary<string, object> edit)
        {
            try
            {
                MoUpdatePlan.Build(MoUpdateReq.Parse(Head(null), new object[] { edit }), snap);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400);
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
