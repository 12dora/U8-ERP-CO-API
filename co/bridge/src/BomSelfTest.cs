using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的物料清单部分：只测纯逻辑（请求校验、新增行号、修改时叠加到现有行），不连库、不建 COM。
    internal static class BomSelfTest
    {
        public static void Run()
        {
            CheckCreate();
            CheckMerge();
            CheckOptional();
            CheckSame();
            CheckCreateReadback();
            CheckTouchedOptional();
            CheckRefused();
            CheckSubs();
            CheckNested();
            CheckSubsKeep();
            CheckSubsRefused();
        }

        static void CheckCreate()
        {
            BomAsk ask = BomReq.ParseCreate(Map("inv_code", "P1"), new object[]
            {
                Map("inv_code", "C1", "base_qty_n", 1, "sort_seq", 30),
                Map("inv_code", "C2", "base_qty_n", 2.5)
            });
            ask.EffDate = "2026-09-28";
            List<BomRow> rows = BomMerge.Build(ask);
            Expect("bom create seqs", rows.Count == 2 && rows[0].Seq == 30 && rows[1].Seq == 40);
            Expect("bom create defaults", rows[1].QtyN == 2.5m && rows[1].QtyD == 1m && rows[1].Wip == 3
                && rows[1].OpSeq == "0000" && rows[1].EffBeg == "2026-09-28" && rows[1].PlanRate == 100m
                && rows[1].Optional == 0 && rows[1].Mutex == 2);
        }

        static void CheckMerge()
        {
            BomAsk ask = BomReq.ParseUpdate(Map("version_desc", "v2"), new object[]
            {
                Map("op", "update", "sort_seq", 10, "base_qty_n", 2),
                Map("op", "delete", "sort_seq", 20),
                Map("op", "add", "inv_code", "C3", "base_qty_n", 1)
            });
            BomMerge.Merge(ask, Head(), Current());
            Expect("bom merge rows", ask.Rows.Count == 2 && ask.Rows[0].Seq == 10 && ask.Rows[1].Seq == 30);
            Expect("bom merge keeps flags", ask.Rows[0].QtyN == 2m && ask.Rows[0].Mutex == 1 && ask.Rows[0].Optional == 1
                && ask.Rows[0].Wh == "13");
            Expect("bom merge head", ask.VersionDesc == "v2" && ask.EffDate == "2026-01-01" && ask.Version == 40
                && ask.PartId == 1000000021 && ask.Rows[1].EffBeg == "2026-01-01");
        }

        // 写后回读核对要比到桥送给 U8 的每个字段：只改损耗率、标志这类也要能看出没写进去。
        static void CheckSame()
        {
            BomAsk ask = BomReq.ParseUpdate(Map("parent_scrap", 2), Lines(Map("op", "update", "sort_seq", 10, "comp_scrap", 1.5)));
            BomMerge.Merge(ask, Head(), Current());
            List<Dictionary<string, object>> after = Current();
            after[0]["comp_scrap"] = "1.500";
            Dictionary<string, object> head = Head();
            head["parent_scrap"] = "2.000";
            Expect("bom same all", BomEdit.Same(head, after, ask));
            Expect("bom same line scrap", !BomEdit.Same(head, Current(), ask));
            Expect("bom same parent scrap", !BomEdit.Same(Head(), after, ask));
            after[0]["optional_flag"] = "0";
            Expect("bom same optional", !BomEdit.Same(head, after, ask));
            after[0]["optional_flag"] = "1";
            after[0]["mutex_rule"] = "2";
            Expect("bom same mutex", !BomEdit.Same(head, after, ask));
            after[0]["mutex_rule"] = "1";
            after[1]["cost_wip_rel"] = "1";
            Expect("bom same flags", !BomEdit.Same(head, after, ask));
        }

        static void CheckCreateReadback()
        {
            BomAsk ask = BomReq.ParseCreate(Map("inv_code", "P1"),
                Lines(Map("inv_code", "C1", "base_qty_n", 1, "sort_seq", 10, "optional", true, "mutex_rule", 1)));
            List<BomRow> rows = BomMerge.Build(ask);
            List<Dictionary<string, object>> lines = new List<Dictionary<string, object>>();
            lines.Add(Map("sort_seq", "10", "inv_code", "C1", "base_qty_n", "1",
                "optional_flag", "1", "mutex_rule", "1"));
            Expect("bom create readback flags", BomCreate.SameRows(lines, rows));
            lines[0]["optional_flag"] = "0";
            Expect("bom create optional dropped", !BomCreate.SameRows(lines, rows));
            lines[0]["optional_flag"] = "1";
            lines[0]["mutex_rule"] = "2";
            Expect("bom create mutex dropped", !BomCreate.SameRows(lines, rows));
        }

        static void CheckRefused()
        {
            Refused("bom update inv", delegate { BomReq.ParseUpdate(null, Lines(Map("op", "update", "sort_seq", 10, "inv_code", "X"))); });
            Refused("bom delete extra", delegate { BomReq.ParseUpdate(null, Lines(Map("op", "delete", "sort_seq", 10, "remark", "x"))); });
            Refused("bom dup seq", delegate
            {
                BomReq.ParseUpdate(null, new object[] { Map("op", "delete", "sort_seq", 10), Map("op", "update", "sort_seq", 10, "base_qty_n", 1) });
            });
            Refused("bom head", delegate { BomReq.ParseCreate(Map("inv_code", "P1", "code", "x"), Lines(Map("inv_code", "C1", "base_qty_n", 1))); });
            Refused("bom merge missing", delegate { MergeOf(Map("op", "update", "sort_seq", 99, "base_qty_n", 1)); });
            Refused("bom merge taken", delegate { MergeOf(Map("op", "add", "inv_code", "C3", "base_qty_n", 1, "sort_seq", 20)); });
            Refused("bom merge empty", delegate
            {
                BomAsk ask = BomReq.ParseUpdate(null, new object[] { Map("op", "delete", "sort_seq", 10), Map("op", "delete", "sort_seq", 20) });
                BomMerge.Merge(ask, Head(), Current());
            });
            Refused("bom mutex without optional", delegate
            {
                BomReq.ParseCreate(Map("inv_code", "P1"), Lines(Map("inv_code", "C1", "base_qty_n", 1, "mutex_rule", 1)));
            });
            Refused("bom mutex optional false", delegate
            {
                BomReq.ParseCreate(Map("inv_code", "P1"),
                    Lines(Map("inv_code", "C1", "base_qty_n", 1, "optional", false, "mutex_rule", 1)));
            });
            Refused("bom optional int", delegate
            {
                BomReq.ParseCreate(Map("inv_code", "P1"), Lines(Map("inv_code", "C1", "base_qty_n", 1, "optional", 1)));
            });
            Refused("bom mutex range", delegate
            {
                BomReq.ParseCreate(Map("inv_code", "P1"),
                    Lines(Map("inv_code", "C1", "base_qty_n", 1, "optional", true, "mutex_rule", 5)));
            });
            Refused("bom update mutex optional false", delegate
            {
                BomReq.ParseUpdate(null, Lines(Map("op", "update", "sort_seq", 10, "optional", false, "mutex_rule", 3)));
            });
            Refused("bom update mutex on required", delegate
            {
                MergeOf(Map("op", "update", "sort_seq", 20, "mutex_rule", 3));
            });
        }

        // 选配 / 互斥：新增缺省 Mutex=2；修改未送保持现值；optional false 强制 Mutex=2。
        static void CheckOptional()
        {
            BomAsk ask = BomReq.ParseCreate(Map("inv_code", "P1"), new object[]
            {
                Map("inv_code", "C1", "base_qty_n", 1, "optional", true, "mutex_rule", 1),
                Map("inv_code", "C2", "base_qty_n", 1, "optional", true)
            });
            ask.EffDate = "2026-09-28";
            List<BomRow> rows = BomMerge.Build(ask);
            Expect("bom optional mutex", rows[0].Optional == 1 && rows[0].Mutex == 1);
            Expect("bom optional default mutex", rows[1].Optional == 1 && rows[1].Mutex == 2);
            BomAsk edit = BomReq.ParseUpdate(null, new object[]
            {
                Map("op", "update", "sort_seq", 10, "optional", true, "mutex_rule", 3),
                Map("op", "update", "sort_seq", 20, "optional", false)
            });
            BomMerge.Merge(edit, Head(), Current());
            Expect("bom update optional", edit.Rows[0].Optional == 1 && edit.Rows[0].Mutex == 3);
            Expect("bom clear optional", edit.Rows[1].Optional == 0 && edit.Rows[1].Mutex == 2);
            BomAsk mutexOnly = BomReq.ParseUpdate(null, Lines(Map("op", "update", "sort_seq", 10, "mutex_rule", 3)));
            BomMerge.Merge(mutexOnly, Head(), Current());
            Expect("bom update mutex keep optional", mutexOnly.Rows[0].Optional == 1 && mutexOnly.Rows[0].Mutex == 3);
        }

        // 选配母件闸门只看本次新增或改过的行：只改表头或非选配行时，现有选配行不触发。
        static void CheckTouchedOptional()
        {
            BomAsk head = BomReq.ParseUpdate(Map("version_desc", "v2"), null);
            BomMerge.Merge(head, Head(), Current());
            Expect("bom optional parent skips head-only", !BomSql.AnyOptional(BomEdit.Touched(head)));
            Expect("bom optional still on existing", BomSql.AnyOptional(head.Rows));
            BomAsk other = BomReq.ParseUpdate(null, Lines(Map("op", "update", "sort_seq", 20, "remark", "n")));
            BomMerge.Merge(other, Head(), Current());
            Expect("bom optional parent skips untouched", !BomSql.AnyOptional(BomEdit.Touched(other)));
            BomAsk add = BomReq.ParseUpdate(null,
                Lines(Map("op", "add", "inv_code", "C3", "base_qty_n", 1, "optional", true)));
            BomMerge.Merge(add, Head(), Current());
            Expect("bom optional parent checks add", BomSql.AnyOptional(BomEdit.Touched(add)));
            BomAsk clear = BomReq.ParseUpdate(null, Lines(Map("op", "update", "sort_seq", 10, "optional", false)));
            BomMerge.Merge(clear, Head(), Current());
            Expect("bom optional parent skips cleared", !BomSql.AnyOptional(BomEdit.Touched(clear)));
        }

        // 替代料、定位符：新增按缺省补齐（优先级 1、系数 1、日期取子件行）；修改没带保留现有的，带了整组替换，空数组清空。
        static void CheckSubs()
        {
            BomAsk ask = BomReq.ParseCreate(Map("inv_code", "P1"), Lines(Map("inv_code", "C1", "base_qty_n", 1,
                "substitutes", new object[] { Map("inv_code", "S1"), Map("inv_code", "S2", "sequence", 2, "factor", 0.5,
                    "replace_flag", true, "eff_begin", "2026-10-01", "eff_end", "2027-12-31") },
                "locators", new object[] { Map("loc", "A1") })));
            ask.EffDate = "2026-09-28";
            List<BomRow> rows = BomMerge.Build(ask);
            Expect("bom subs count", rows[0].Subs.Count == 2);
            Expect("bom subs defaults", SubIs(rows[0].Subs[0], 1, 1m, 0, "2026-09-28", BomRow.EndDate));
            Expect("bom subs given", SubIs(rows[0].Subs[1], 2, 0.5m, 1, "2026-10-01", "2027-12-31"));
            Expect("bom locs", rows[0].Locs.Count == 1 && rows[0].Locs[0] == "A1");
            CheckSubsReadback(rows);
        }

        static bool SubIs(BomSub sub, int seq, decimal factor, int replace, string beg, string end)
        {
            return sub.Seq == seq && sub.Factor == factor && sub.Replace == replace && sub.EffBeg == beg && sub.EffEnd == end;
        }

        // 回读核对：替代料按存货对上（顺序、大小写不论），定位符少了就不符。
        static void CheckSubsReadback(List<BomRow> rows)
        {
            List<Dictionary<string, object>> back = new List<Dictionary<string, object>>();
            back.Add(Map("sort_seq", "10", "inv_code", "C1", "substitutes", new List<object> { SubLine("S2", 2, 0.5m, true,
                "2026-10-01", "2027-12-31"), SubLine("s1", 1, 1m, false, "2026-09-28", BomRow.EndDate) },
                "locators", new List<object> { Map("sort_seq", 10, "loc", "A1") }));
            Expect("bom subs readback", BomSubs.SameAll(back, rows));
            ((List<object>)back[0]["locators"]).Clear();
            Expect("bom locs dropped", !BomSubs.SameAll(back, rows));
        }

        // 数组字段登记（NestedFields）：物料清单只有明细行的 substitutes、locators 可以是数组。
        static void CheckNested()
        {
            VoucherKind bom = Kinds.Find(BomRoutes.KindName);
            Expect("bom nested lines", NestedFields.Allowed(bom, false, "Substitutes", new List<object>())
                && NestedFields.Allowed(bom, false, "locators", new object[0]) && !NestedFields.Allowed(bom, true, "substitutes", new object[0])
                && !NestedFields.Allowed(bom, false, "remark", new object[0]) && !NestedFields.Allowed(bom, false, "locators", "A1"));
        }

        static void CheckSubsKeep()
        {
            List<Dictionary<string, object>> current = Current();
            current[0]["substitutes"] = new List<object> { SubLine("S1", 1, 1m, false, "2026-01-01", BomRow.EndDate) };
            current[0]["locators"] = new List<object> { Map("sort_seq", 10, "loc", "A1") };
            BomAsk keep = BomReq.ParseUpdate(null, Lines(Map("op", "update", "sort_seq", 10, "base_qty_n", 3)));
            BomMerge.Merge(keep, Head(), current);
            Expect("bom subs kept", keep.Rows[0].Subs.Count == 1 && keep.Rows[0].Subs[0].InvCode == "S1"
                && keep.Rows[0].Locs.Count == 1);
            List<Dictionary<string, object>> after = Current();
            after[0]["base_qty_n"] = "3.000000";
            after[0]["substitutes"] = current[0]["substitutes"];
            after[0]["locators"] = current[0]["locators"];
            Expect("bom subs kept readback", BomEdit.Same(Head(), after, keep));
            after[0]["substitutes"] = new List<object>();
            Expect("bom subs lost readback", !BomEdit.Same(Head(), after, keep));
            BomAsk swap = BomReq.ParseUpdate(null, Lines(Map("op", "update", "sort_seq", 10,
                "substitutes", new object[] { Map("inv_code", "S9", "factor", 2) })));
            BomMerge.Merge(swap, Head(), current);
            Expect("bom subs replaced", swap.Rows[0].Subs.Count == 1 && swap.Rows[0].Subs[0].InvCode == "S9"
                && swap.Rows[0].Subs[0].Factor == 2m && swap.Rows[0].Subs[0].EffBeg == "2026-01-01" && swap.Rows[0].Locs.Count == 1);
            Expect("bom subs replace touches", BomEdit.Touched(swap).Count == 1);
            BomAsk clear = BomReq.ParseUpdate(null, Lines(Map("op", "update", "sort_seq", 10,
                "substitutes", new object[0], "locators", new object[0])));
            BomMerge.Merge(clear, Head(), current);
            Expect("bom subs cleared", clear.Rows[0].Subs.Count == 0 && clear.Rows[0].Locs.Count == 0);
            Expect("bom subs clear differs", !BomEdit.Same(Head(), current, clear));
        }

        static void CheckSubsRefused()
        {
            Refused("bom subs dup", delegate { SubsOf(Map("inv_code", "S1"), Map("inv_code", "s1")); });
            Refused("bom subs factor", delegate { SubsOf(Map("inv_code", "S1", "factor", 0)); });
            Refused("bom subs sequence", delegate { SubsOf(Map("inv_code", "S1", "sequence", 10001)); });
            Refused("bom subs flag", delegate { SubsOf(Map("inv_code", "S1", "replace_flag", 1)); });
            Refused("bom subs key", delegate { SubsOf(Map("inv_code", "S1", "qty", 1)); });
            Refused("bom subs no inv", delegate { SubsOf(Map("factor", 1)); });
            Refused("bom subs not list", delegate
            {
                BomReq.ParseCreate(Map("inv_code", "P1"), Lines(Map("inv_code", "C1", "base_qty_n", 1, "substitutes", "S1")));
            });
            Refused("bom locs dup", delegate
            {
                BomReq.ParseCreate(Map("inv_code", "P1"), Lines(Map("inv_code", "C1", "base_qty_n", 1,
                    "locators", new object[] { Map("loc", "A1"), Map("loc", "A1") })));
            });
            Refused("bom subs dates", delegate
            {
                BomAsk ask = BomReq.ParseCreate(Map("inv_code", "P1"), Lines(Map("inv_code", "C1", "base_qty_n", 1,
                    "substitutes", new object[] { Map("inv_code", "S1", "eff_begin", "2027-01-01", "eff_end", "2026-12-31") })));
                ask.EffDate = "2026-09-28";
                BomMerge.Build(ask);
            });
        }

        static void SubsOf(params object[] subs)
        {
            BomReq.ParseCreate(Map("inv_code", "P1"), Lines(Map("inv_code", "C1", "base_qty_n", 1, "substitutes", subs)));
        }

        // 回读（BomSubs.Attach）里一条替代料的形状。
        static Dictionary<string, object> SubLine(string inv, int seq, decimal factor, bool replace, string beg, string end)
        {
            return Map("sequence", seq, "inv_code", inv, "factor", factor, "replace_flag", replace, "eff_begin", beg, "eff_end", end);
        }

        static void MergeOf(Dictionary<string, object> line)
        {
            BomMerge.Merge(BomReq.ParseUpdate(null, Lines(line)), Head(), Current());
        }

        static Dictionary<string, object> Head()
        {
            return Map("inv_code", "P1", "version", "40", "part_id", "1000000021", "version_desc", "v1",
                "eff_date", "2026-01-01", "parent_scrap", "0.000");
        }

        static List<Dictionary<string, object>> Current()
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            rows.Add(Map("sort_seq", "10", "inv_code", "C1", "base_qty_n", "1.000000", "base_qty_d", "1.000000",
                "wip_type", "3", "wh_code", "13", "eff_beg", "2026-01-01", "eff_end", "2099-12-31",
                "optional_flag", "1", "mutex_rule", "1"));
            rows.Add(Map("sort_seq", "20", "inv_code", "C2", "base_qty_n", "1.000000", "base_qty_d", "1.000000",
                "eff_beg", "2026-01-01"));
            return rows;
        }

        static object[] Lines(Dictionary<string, object> line)
        {
            return new object[] { line };
        }

        static Dictionary<string, object> Map(params object[] pairs)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                map[(string)pairs[i]] = pairs[i + 1];
            }
            return map;
        }

        static void Refused(string name, Action run)
        {
            try
            {
                run();
            }
            catch (BridgeException ex)
            {
                if (ex.Status == 400)
                {
                    return;
                }
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
