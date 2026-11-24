using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的合并检验部分：source_ids、lines 的解析，合格数量，订单号连接，回读核对。不连库、不建 COM。
    internal static class QmMergeSelfTest
    {
        public static void Run()
        {
            CheckIds();
            CheckLines();
            CheckMoCodes();
            CheckMismatch();
            CheckLocks();
            CheckRules();
            CheckPieces();
        }

        static void CheckRules()
        {
            Expect("qmm rules default", QmMergeRules.Text(QmMergeRules.Parse(null)) == "0");
            Expect("qmm rules sorted", QmMergeRules.Text(QmMergeRules.Parse(new ArrayList(new object[] { "2", 0, 1L }))) == "0,1,2");
            ExpectStatus("qmm rules 3", 400, delegate { QmMergeRules.Parse(new ArrayList(new object[] { "3" })); });
            ExpectStatus("qmm rules 9", 400, delegate { QmMergeRules.Parse(new ArrayList(new object[] { 9 })); });
            ExpectStatus("qmm rules dup", 400, delegate { QmMergeRules.Parse(new ArrayList(new object[] { "0", 0 })); });
            ExpectStatus("qmm rules empty", 400, delegate { QmMergeRules.Parse(new ArrayList()); });
            ExpectStatus("qmm rules text", 400, delegate { QmMergeRules.Parse("0"); });
            Dictionary<string, object> a = Peek("x", "D1");
            Dictionary<string, object> b = Peek("x", "D2");
            List<QmMergeRule> free = QmMergeRules.Parse(null);
            Expect("qmm rules free same", QmMergeRules.Mismatch(free, a, b) == "");
            Expect("qmm rules dept", QmMergeRules.Mismatch(QmMergeRules.Parse(new ArrayList(new object[] { 0, 1 })), a, b) == "生产部门");
            Expect("qmm rules free diff", QmMergeRules.Mismatch(free, a, Peek("y", "D1")) == "自由项");
            Expect("qmm rules null blank", QmMergeRules.Mismatch(free, Peek("", "D1"), new Dictionary<string, object>()) == "");
        }

        static Dictionary<string, object> Peek(string free3, string dep)
        {
            Dictionary<string, object> row = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            row["CFREE3"] = free3;
            row["RDEP"] = dep;
            return row;
        }

        static void CheckPieces()
        {
            QmMergeSource whole = Source(1, "", 15000m);
            whole.Line["CUNITID"] = "U2";
            QmMergeSrc.PiecesOf(whole, 15000m, 600m, 25m);
            ExpectDec("qmm pieces whole", 600m, whole.Num);
            ExpectDec("qmm pieces whole reg", 600m, whole.RegNum);
            QmMergeSource part = Source(2, "", 10m);
            part.Ask.Dis = 2.5m;
            part.Line["CUNITID"] = "U2";
            QmMergeSrc.PiecesOf(part, 30m, 12m, 2.5m);
            ExpectDec("qmm pieces part", 4m, part.Num);
            ExpectDec("qmm pieces part dis", 1m, part.DisNum);
            ExpectDec("qmm pieces part reg", 3m, part.RegNum);
            QmMergeSource none = Source(3, "", 10m);
            QmMergeSrc.PiecesOf(none, 10m, 4m, 2.5m);
            ExpectDec("qmm pieces no unit", 0m, none.Num);
        }

        static void CheckIds()
        {
            int[] ids = QmMergeReq.ParseIds(new ArrayList(new object[] { 7, 9L }));
            Expect("qmm ids", ids.Length == 2 && ids[0] == 7 && ids[1] == 9);
            ExpectStatus("qmm ids one", 400, delegate { QmMergeReq.ParseIds(new ArrayList(new object[] { 7 })); });
            ExpectStatus("qmm ids dup", 400, delegate { QmMergeReq.ParseIds(new ArrayList(new object[] { 7, 7 })); });
            ExpectStatus("qmm ids zero", 400, delegate { QmMergeReq.ParseIds(new ArrayList(new object[] { 7, 0 })); });
            ExpectStatus("qmm ids text", 400, delegate { QmMergeReq.ParseIds(new ArrayList(new object[] { 7, "8" })); });
            ExpectStatus("qmm ids map", 400, delegate { QmMergeReq.ParseIds(new Dictionary<string, object>()); });
            ArrayList many = new ArrayList();
            for (int i = 1; i <= QmMergeReq.Max + 1; i++)
            {
                many.Add(i);
            }
            ExpectStatus("qmm ids many", 400, delegate { QmMergeReq.ParseIds(many); });
        }

        static void CheckLines()
        {
            QmSpec spec = QmSpec.Find(QmMergeReq.Kind);
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["cCheckPersonCode"] = "P900001";
            int[] ids = new int[] { 11, 12 };
            List<QmMergeLine> merged = new List<QmMergeLine>();
            QmMergeReq.Parse(spec, head, ids, new object[0], merged);
            Expect("qmm whole", merged.Count == 2 && merged[0].Whole && merged[1].Id == 12);
            ExpectDec("qmm whole split", 15m, QmMergeReq.Split(merged[0], 15m).Reg);
            Dictionary<string, object> a = new Dictionary<string, object>();
            a["quantity"] = 10;
            a["fDisQuantity"] = 2.5d;
            Dictionary<string, object> b = new Dictionary<string, object>();
            b["source_line_id"] = 12;
            b["fConQuantiy"] = 1;
            merged.Clear();
            QmMergeReq.Parse(spec, head, ids, new object[] { a, b }, merged);
            Expect("qmm given", !merged[0].Whole && merged[1].Whole);
            ExpectDec("qmm given reg", 7.5m, QmMergeReq.Split(merged[0], merged[0].Qty).Reg);
            ExpectDec("qmm whole con", 4m, QmMergeReq.Split(merged[1], 5m).Reg);
            ExpectStatus("qmm short", 400, delegate { QmMergeReq.Parse(spec, head, ids, new object[] { a }, new List<QmMergeLine>()); });
            b["source_line_id"] = 99;
            ExpectStatus("qmm wrong id", 400, delegate { QmMergeReq.Parse(spec, head, ids, new object[] { a, b }, new List<QmMergeLine>()); });
            b.Remove("source_line_id");
            b["cwhcode"] = "01";
            ExpectStatus("qmm bad key", 400, delegate { QmMergeReq.Parse(spec, head, ids, new object[] { a, b }, new List<QmMergeLine>()); });
            head.Remove("cCheckPersonCode");
            ExpectStatus("qmm checker", 400, delegate { QmMergeReq.Parse(spec, head, ids, new object[0], new List<QmMergeLine>()); });
        }

        static void CheckMoCodes()
        {
            QmMergeSet set = new QmMergeSet();
            set.Sources.Add(Source(1, "MO-1", 5m));
            set.Sources.Add(Source(2, "MO-2", 5m));
            set.Sources.Add(Source(3, "MO-1", 5m));
            set.Sources.Add(Source(4, "", 5m));
            Expect("qmm mocodes", QmMergeSrc.MoCodes(set) == "MO-1,MO-2");
            set.Lead = set.Sources[1];
            Expect("qmm mocodes lead", QmMergeSrc.MoCodes(set) == "MO-2,MO-1");
            QmMergeSet wide = new QmMergeSet();
            for (int i = 0; i < 30; i++)
            {
                wide.Sources.Add(Source(i + 1, "DEMO-" + i.ToString("D10", System.Globalization.CultureInfo.InvariantCulture), 1m));
            }
            string codes = QmMergeSrc.MoCodes(wide);
            Expect("qmm mocodes cap", codes.Length <= 255 && !codes.EndsWith(",", StringComparison.Ordinal));
        }

        static void CheckMismatch()
        {
            QmMergeSet set = new QmMergeSet();
            set.Sources.Add(Source(11, "", 5m));
            set.Sources.Add(Source(12, "", 2.5m));
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            rows.Add(Detail("12", "2.500000"));
            rows.Add(Detail("11", "5.000000"));
            Expect("qmm detail ok", QmMergeSaved.Mismatch(set, rows) == "");
            rows[0]["qty"] = "3";
            Expect("qmm detail qty", QmMergeSaved.Mismatch(set, rows).Length > 0);
            rows.RemoveAt(0);
            Expect("qmm detail count", QmMergeSaved.Mismatch(set, rows).Length > 0);
            rows.Add(Detail("13", "2.5"));
            Expect("qmm detail other", QmMergeSaved.Mismatch(set, rows).Length > 0);
            Expect("qmm sources other", !QmMergeSaved.SameSources(set, rows));
            rows[1]["src"] = "12";
            Expect("qmm sources same", QmMergeSaved.SameSources(set, rows));
        }

        static void CheckLocks()
        {
            string[] keys = QmMergeReq.LockKeys();
            Expect("qmm locks", keys.Length == 2 && keys[0] == "new:qm_product_check" && keys[1] == "new:qm_product_inspect");
        }

        static QmMergeSource Source(int id, string mo, decimal qty)
        {
            QmMergeSource src = new QmMergeSource();
            src.Ask = new QmLineAsk();
            src.Ask.SourceLineId = id;
            src.Ask.Qty = qty;
            src.Line = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            src.Line["CPROORDERCODE"] = mo;
            return src;
        }

        static Dictionary<string, object> Detail(string src, string qty)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["src"] = src;
            row["qty"] = qty;
            return row;
        }

        static void ExpectStatus(string name, int status, Action act)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                if (ex.Status == status)
                {
                    return;
                }
            }
            throw new InvalidOperationException(name);
        }

        static void ExpectDec(string name, decimal want, decimal got)
        {
            if (want != got)
            {
                throw new InvalidOperationException(name);
            }
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
