using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的下游连带部分（vouchers/so_defines，cascade=true）：下游判断（unchanged / updated / 一列不同的 mixed_sources /
    // 无来源 / locked 及其顺序）、下游列缺失或非文本的 400、cascade 列表形状、下游 SQL 片段和张数上限。不连库。
    // 由 VoucherSoDefinesSelfTest.Run 调用；来源解析、列宽 400 见 VoucherSoDefinesLinksSelfTest。
    internal static class VoucherSoDefinesCascadeSelfTest
    {
        public static void Run()
        {
            CheckDown();
            CheckDownOrder();
            CheckColumns();
            CheckEntries();
            CheckSql();
            VoucherSoDefinesLinksSelfTest.Run();
        }

        static void CheckDown()
        {
            SoDefinesAsk ask = VoucherSoDefinesSelfTest.Ask2("文本A", "DEMO-001", "", "");
            Down("down self only", ask, Doc(null, null, "", Src(7, null, null)), "updated", null);
            Down("down unchanged", ask, Doc(" 文本A", "DEMO-001 ", "", Src(7, null, null)), "unchanged", null);
            Down("down other agrees", ask, Doc("文本B", null, "", Src(7, null, null), Src(8, "文本A", "DEMO-001")), "updated", null);
            Down("down one column differs", ask, Doc("文本B", null, "", Src(7, null, null), Src(8, "文本A", "DEMO-002")),
                "skipped", "mixed_sources");
            Down("down other case", ask, Doc("文本B", null, "", Src(7, null, null), Src(8, "文本A", "demo-001")),
                "skipped", "mixed_sources");
            Down("down no source line", ask, Doc("文本B", null, "", Src(7, null, null), Src(0, null, null)), "skipped", "mixed_sources");
            Down("down no sources", ask, Doc("文本B", null, ""), "skipped", "mixed_sources");
            Down("down locked", ask, Doc("文本B", null, "李四", Src(7, null, null)), "skipped", "locked");
            Down("down own lock", ask, Doc("文本B", null, " 张三 ", Src(7, null, null)), "updated", null);
        }

        // 顺序：已是目标时不看来源和锁（unchanged）；来源不一致先于锁定。
        static void CheckDownOrder()
        {
            SoDefinesAsk ask = VoucherSoDefinesSelfTest.Ask2("文本A", "DEMO-001", "", "");
            Down("order unchanged mixed", ask, Doc("文本A", "DEMO-001", "李四", Src(8, "X", "Y")), "unchanged", null);
            Down("order mixed before lock", ask, Doc("文本B", null, "李四", Src(8, "X", "Y")), "skipped", "mixed_sources");
        }

        // 下游表缺请求列或不是文本列：400，field 为 fields.cdefineN；都在时放行。
        static void CheckColumns()
        {
            SoDefinesAsk ask = VoucherSoDefinesSelfTest.Ask2("文本A", "DEMO-001", "", "");
            Dictionary<string, SoDefinesColumn> cols = new Dictionary<string, SoDefinesColumn>(StringComparer.OrdinalIgnoreCase);
            cols["DispatchList.cDefine1"] = VoucherSoDefinesSelfTest.Col("cDefine1", true, 20);
            cols["DispatchList.cDefine2"] = VoucherSoDefinesSelfTest.Col("cDefine2", true, 20);
            List<SoDownDoc> docs = new List<SoDownDoc>(new SoDownDoc[] { Doc(null, null, "") });
            VoucherSoDefinesCascade.CheckColumns(ask, docs, cols);
            VoucherSoDefinesCascade.CheckColumns(ask, new List<SoDownDoc>(), new Dictionary<string, SoDefinesColumn>());
            cols["DispatchList.cDefine2"].Text = false;
            BadColumns("down not text", ask, docs, cols, "fields.cdefine2");
            cols.Remove("DispatchList.cDefine2");
            BadColumns("down missing", ask, docs, cols, "fields.cdefine2");
        }

        static void CheckEntries()
        {
            SoDefinesAsk ask = VoucherSoDefinesSelfTest.Ask2("文本A", "DEMO-001", "", "");
            SoDownDoc a = Doc(null, "文本B", "", Src(7, null, null));
            SoDownDoc b = Doc("文本B", null, "", Src(8, "X", "Y"));
            b.Kind = VoucherSoDefinesDown.Kinds[2];
            VoucherSoDefinesCascade.DecideDown(ask, a, "张三");
            VoucherSoDefinesCascade.DecideDown(ask, b, "张三");
            Dictionary<string, object> body = VoucherSoDefinesCascade.WithCascade(new Dictionary<string, object>(), ask,
                new List<SoDownDoc>(new SoDownDoc[] { a, b }));
            List<object> list = (List<object>)body[VoucherSoDefines.CascadeKey];
            Dictionary<string, object> first = (Dictionary<string, object>)list[0];
            Dictionary<string, object> second = (Dictionary<string, object>)list[1];
            Dictionary<string, object> before = (Dictionary<string, object>)first["before"];
            Expect("entry first", (string)first["type"] == "dispatch" && (int)first["id"] == 11 && (string)first["code"] == "FH0001"
                && (string)first["result"] == "updated" && !first.ContainsKey("reason"));
            Expect("entry before", before["cdefine1"] == null && (string)before["cdefine2"] == "文本B" && before.Count == 2);
            Expect("entry second", (string)second["type"] == "sale_invoice" && (string)second["result"] == "skipped"
                && (string)second["reason"] == "mixed_sources");
            Expect("entry none", !VoucherSoDefinesCascade.WithCascade(new Dictionary<string, object>(), ask, null)
                .ContainsKey(VoucherSoDefines.CascadeKey));
        }

        static void CheckSql()
        {
            SoDownKind[] kinds = VoucherSoDefinesDown.Kinds;
            List<string> keys = new List<string>(new string[] { "cdefine1", "cdefine12" });
            Expect("kinds", kinds.Length == 3 && kinds[0].Table == "DispatchList" && kinds[1].Table == "rdrecord32"
                && kinds[2].Table == "SaleBillVouch" && kinds[1].Type == "sale_out");
            Expect("kinds links", kinds[0].From.Contains("s1.iSOsID=NULLIF(l.iSOsID,0)")
                && kinds[1].From.Contains("l.iDLsID=NULLIF(r.iDLsID,0)") && !kinds[2].From.Contains("ISNULL("));
            Eq("head full", VoucherSoDefinesDown.HeadSql(kinds[0], keys, true, true),
                "SELECT LTRIM(RTRIM(ISNULL(cDLCode,''))) AS code, cDefine1 AS cdefine1, cDefine12 AS cdefine12,"
                + " LTRIM(RTRIM(ISNULL(cLocker,''))) AS locker, CONVERT(varchar(20), CONVERT(bigint, ufts)) AS ts"
                + " FROM DispatchList WITH (UPDLOCK, ROWLOCK) WHERE DLID=?");
            Eq("head bare", VoucherSoDefinesDown.HeadSql(kinds[1], keys, false, false),
                "SELECT LTRIM(RTRIM(ISNULL(cCode,''))) AS code, cDefine1 AS cdefine1, cDefine12 AS cdefine12, '' AS locker, '' AS ts"
                + " FROM rdrecord32 WITH (UPDLOCK, ROWLOCK) WHERE ID=?");
            Eq("order sql", VoucherSoDefinesDown.OrderSql(keys),
                "SELECT cDefine1 AS cdefine1, cDefine12 AS cdefine12 FROM SO_SOMain WITH (UPDLOCK, ROWLOCK) WHERE ID=?");
            Eq("update ts", VoucherSoDefinesDown.UpdateSql(kinds[2], keys, true),
                "UPDATE SaleBillVouch SET cDefine1=?, cDefine12=? WHERE SBVID=? AND ufts=CONVERT(binary(8), CONVERT(bigint, ?))");
            Eq("update bare", VoucherSoDefinesDown.UpdateSql(kinds[1], new List<string>(new string[] { "cdefine3" }), false),
                "UPDATE rdrecord32 SET cDefine3=? WHERE ID=?");
            VoucherSoDefinesDown.CheckCap(VoucherSoDefinesDown.Cap, "x");
            try
            {
                VoucherSoDefinesDown.CheckCap(VoucherSoDefinesDown.Cap + 1, "x");
            }
            catch (BridgeException ex)
            {
                Expect("cap", ex.Status == 409 && ex.Code == "too_many_downstream");
                return;
            }
            throw new InvalidOperationException("so_defines cascade cap not refused");
        }

        static void Down(string name, SoDefinesAsk ask, SoDownDoc doc, string result, string reason)
        {
            VoucherSoDefinesCascade.DecideDown(ask, doc, "张三");
            Expect(name + " (" + doc.Result + "/" + doc.Reason + ")", doc.Result == result && doc.Reason == reason);
        }

        static void BadColumns(string name, SoDefinesAsk ask, List<SoDownDoc> docs, Dictionary<string, SoDefinesColumn> cols,
            string field)
        {
            try
            {
                VoucherSoDefinesCascade.CheckColumns(ask, docs, cols);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request" && ex.Field == field);
                return;
            }
            throw new InvalidOperationException("so_defines cascade " + name + " not refused");
        }

        static SoDownDoc Doc(string v1, string v2, string locker, params SoDownSource[] sources)
        {
            SoDownDoc doc = new SoDownDoc();
            doc.Kind = VoucherSoDefinesDown.Kinds[0];
            doc.Id = 11;
            doc.Code = "FH0001";
            doc.Values["cdefine1"] = v1;
            doc.Values["cdefine2"] = v2;
            doc.Locker = locker;
            doc.Ts = "200";
            doc.Sources.AddRange(sources);
            return doc;
        }

        static SoDownSource Src(int id, string v1, string v2)
        {
            SoDownSource s = new SoDownSource();
            s.Id = id;
            s.Values["cdefine1"] = v1;
            s.Values["cdefine2"] = v2;
            return s;
        }

        static void Eq(string name, object got, object want)
        {
            Expect(name + " (" + got + " != " + want + ")", object.Equals(got, want));
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("so_defines cascade " + name);
            }
        }
    }
}
