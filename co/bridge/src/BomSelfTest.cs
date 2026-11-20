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
