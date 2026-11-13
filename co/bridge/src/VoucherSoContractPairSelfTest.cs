using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的成对设置部分（vouchers/so_contract，mode=pair）：请求解析与拒绝（mode、只在 pair 时可用的键、pair 不收的键）、
    // 成对判断（覆盖、已是目标、大小写不同也写、expect 不符的 detail.current）、下游判断（unchanged / mixed_sources / locked /
    // updated 及其顺序）、cascade 列表形状、下游 SQL 片段和张数上限。不连库。由 VoucherSoContractSelfTest.Run 调用；
    // 来源解析、列宽 400 见 VoucherSoContractLinksSelfTest。
    internal static class VoucherSoContractPairSelfTest
    {
        const string K1 = VoucherSoContract.ContractKey;
        const string K2 = VoucherSoContract.InternalKey;

        public static void Run()
        {
            CheckParse();
            CheckParseRejects();
            CheckDecide();
            CheckDown();
            CheckDownOrder();
            CheckEntries();
            CheckSql();
            VoucherSoContractLinksSelfTest.Run();
        }

        static void CheckParse()
        {
            SoContractAsk ask = VoucherSoContract.Parse(Body("KH-2026-0001", "HT202601001", Exp(null, "无")));
            Expect("parse pair", ask.Pair && !ask.Cascade && !ask.Replace);
            Expect("parse values", ask.ContractNo == "KH-2026-0001" && ask.CustomerNo == "HT202601001");
            Expect("parse expect", ask.Expect1 == "" && ask.Expect2 == "无");
            Expect("parse key2", ask.Key2 == K2 && new SoContractAsk().Key2 == VoucherSoContract.CustomerKey);
            Dictionary<string, object> body = Body("KH1", "HT1", Exp("", ""));
            body[VoucherSoContract.CascadeKey] = true;
            Expect("parse cascade", VoucherSoContract.Parse(body).Cascade);
            body[VoucherSoContract.CascadeKey] = false;
            Expect("parse cascade false", !VoucherSoContract.Parse(body).Cascade);
        }

        static void CheckParseRejects()
        {
            Bad("mode other", With(Body("KH1", "HT1", Exp("", "")), VoucherSoContract.ModeKey, "swap"));
            Bad("mode null", With(Body("KH1", "HT1", Exp("", "")), VoucherSoContract.ModeKey, null));
            Bad("pair replace", With(Body("KH1", "HT1", Exp("", "")), VoucherSoContract.ReplaceKey, true));
            Bad("pair customer", With(Body("KH1", "HT1", Exp("", "")), VoucherSoContract.CustomerKey, "KH1"));
            Bad("pair cascade text", With(Body("KH1", "HT1", Exp("", "")), VoucherSoContract.CascadeKey, "true"));
            Bad("pair cascade null", With(Body("KH1", "HT1", Exp("", "")), VoucherSoContract.CascadeKey, null));
            Bad("pair no internal", Body("KH1", null, Exp("", "")));
            Bad("pair no contract", Body(null, "HT1", Exp("", "")));
            Bad("pair internal placeholder", Body("KH1", "无", Exp("", "")));
            Bad("pair internal blank", Body("KH1", " ", Exp("", "")));
            Bad("pair internal padded", Body("KH1", "HT1 ", Exp("", "")));
            Bad("pair expect old key", Body("KH1", "HT1", Map(K1, "", VoucherSoContract.CustomerKey, "")));
            Bad("pair expect missing", Body("KH1", "HT1", Map(K1, "")));
            Bad("pair no expect", Body("KH1", "HT1", null));
            Dictionary<string, object> classic = Body("KH1", null, Map(K1, "", VoucherSoContract.CustomerKey, ""));
            classic.Remove(VoucherSoContract.ModeKey);
            VoucherSoContract.Parse(classic);
            Bad("classic internal", With(Copy(classic), K2, "HT1"));
            Bad("classic cascade", With(Copy(classic), VoucherSoContract.CascadeKey, true));
        }

        static void CheckDecide()
        {
            Plan("pair blank", Ask("KH1", "HT1", "", ""), Snap(null, "无"), true, true, true);
            Plan("pair overwrite", Ask("KH1", "HT1", "HT1", "KH1"), Snap("HT1", "KH1"), true, true, true);
            Plan("pair internal only", Ask("KH1", "HT1", "KH1", ""), Snap("KH1", null), true, false, true);
            Plan("pair unchanged", Ask("KH1", "HT1", "kh1", "HT1"), Snap(" KH1 ", "HT1"), false, false, false);
            Plan("pair case differs", Ask("KH1", "HT1", "kh1", "ht1"), Snap("kh1", "HT1"), true, true, false);
            Mismatch("pair expect differs", Ask("KH1", "HT1", "", ""), Snap("HT9", null), "HT9", null);
            Mismatch("pair expect internal", Ask("KH1", "HT1", "HT9", "HT8"), Snap("HT9", "HT7"), "HT9", "HT7");
            SoContractAsk ask = Ask("KH1", "HT1", "", "");
            Dictionary<string, object> body = VoucherSoContract.Body(ask, Snap("KH1", "HT1"), true);
            Dictionary<string, object> current = (Dictionary<string, object>)body["current"];
            Expect("pair body current", (string)current[K1] == "KH1" && (string)current[K2] == "HT1"
                && !current.ContainsKey(VoucherSoContract.CustomerKey) && !body.ContainsKey(VoucherSoContract.CascadeKey));
        }

        static void CheckDown()
        {
            SoContractAsk ask = Ask("KH1", "HT1", "", "");
            Down("down self only", ask, Doc(null, null, "", Src(7, null, null)), "updated", null);
            Down("down unchanged", ask, Doc(" KH1", "HT1 ", "", Src(7, null, null)), "unchanged", null);
            Down("down other agrees", ask, Doc("HT1", null, "", Src(7, null, null), Src(8, "KH1", "HT1")), "updated", null);
            Down("down other differs", ask, Doc("HT1", null, "", Src(7, null, null), Src(8, "KH2", "HT1")), "skipped", "mixed_sources");
            Down("down other case", ask, Doc("HT1", null, "", Src(7, null, null), Src(8, "kh1", "HT1")), "skipped", "mixed_sources");
            Down("down no source line", ask, Doc("HT1", null, "", Src(7, null, null), Src(0, null, null)), "skipped", "mixed_sources");
            Down("down no sources", ask, Doc("HT1", null, ""), "skipped", "mixed_sources");
            Down("down locked", ask, Doc("HT1", null, "李四", Src(7, null, null)), "skipped", "locked");
            Down("down own lock", ask, Doc("HT1", null, " 张三 ", Src(7, null, null)), "updated", null);
            SoDownDoc narrow = Doc("HT1", null, "", Src(7, null, null));
            narrow.Limit2 = 2;
            Down("down narrow still updated", ask, narrow, "updated", null);
        }

        // 顺序：已是这一对时不看来源和锁（unchanged）；来源不一致先于锁定。
        static void CheckDownOrder()
        {
            SoContractAsk ask = Ask("KH1", "HT1", "", "");
            Down("order unchanged mixed", ask, Doc("KH1", "HT1", "李四", Src(8, "X", "Y")), "unchanged", null);
            Down("order mixed before lock", ask, Doc("HT1", null, "李四", Src(8, "X", "Y")), "skipped", "mixed_sources");
        }

        static void CheckEntries()
        {
            SoContractAsk ask = Ask("KH1", "HT1", "", "");
            SoDownDoc a = Doc(null, "HT0", "", Src(7, null, null));
            SoDownDoc b = Doc("HT1", null, "", Src(8, "X", "Y"));
            b.Kind = VoucherSoContractDown.Kinds[2];
            VoucherSoContractPair.DecideDown(ask, a, "张三");
            VoucherSoContractPair.DecideDown(ask, b, "张三");
            Dictionary<string, object> body = VoucherSoContractPair.WithCascade(new Dictionary<string, object>(),
                new List<SoDownDoc>(new SoDownDoc[] { a, b }));
            List<object> list = (List<object>)body[VoucherSoContract.CascadeKey];
            Dictionary<string, object> first = (Dictionary<string, object>)list[0];
            Dictionary<string, object> second = (Dictionary<string, object>)list[1];
            Dictionary<string, object> before = (Dictionary<string, object>)first["before"];
            Expect("entry first", (string)first["type"] == "dispatch" && (int)first["id"] == 11 && (string)first["code"] == "FH0001"
                && (string)first["result"] == "updated" && !first.ContainsKey("reason"));
            Expect("entry before", before[K1] == null && (string)before[K2] == "HT0" && before.Count == 2);
            Expect("entry second", (string)second["type"] == "sale_invoice" && (string)second["result"] == "skipped"
                && (string)second["reason"] == "mixed_sources");
            Expect("entry none", !VoucherSoContractPair.WithCascade(new Dictionary<string, object>(), null)
                .ContainsKey(VoucherSoContract.CascadeKey));
        }

        static void CheckSql()
        {
            SoDownKind[] kinds = VoucherSoContractDown.Kinds;
            Expect("kinds", kinds.Length == 3 && kinds[0].Table == "DispatchList" && kinds[1].Table == "rdrecord32"
                && kinds[2].Table == "SaleBillVouch" && kinds[1].Type == "sale_out");
            Expect("kinds links", kinds[0].From.Contains("s1.iSOsID=NULLIF(l.iSOsID,0)")
                && kinds[1].From.Contains("l.iDLsID=NULLIF(r.iDLsID,0)") && !kinds[2].From.Contains("ISNULL("));
            Eq("head full", VoucherSoContractDown.HeadSql(kinds[0], true, true),
                "SELECT LTRIM(RTRIM(ISNULL(cDLCode,''))) AS code, cDefine1 AS c1, cDefine2 AS c2, LTRIM(RTRIM(ISNULL(cLocker,'')))"
                + " AS locker, CONVERT(varchar(20), CONVERT(bigint, ufts)) AS ts FROM DispatchList WITH (UPDLOCK, ROWLOCK) WHERE DLID=?");
            Eq("head bare", VoucherSoContractDown.HeadSql(kinds[1], false, false),
                "SELECT LTRIM(RTRIM(ISNULL(cCode,''))) AS code, cDefine1 AS c1, cDefine2 AS c2, '' AS locker, '' AS ts"
                + " FROM rdrecord32 WITH (UPDLOCK, ROWLOCK) WHERE ID=?");
            Eq("update ts", VoucherSoContractDown.UpdateSql(kinds[2], true),
                "UPDATE SaleBillVouch SET cDefine1=?, cDefine2=? WHERE SBVID=? AND ufts=CONVERT(binary(8), CONVERT(bigint, ?))");
            Eq("update bare", VoucherSoContractDown.UpdateSql(kinds[1], false), "UPDATE rdrecord32 SET cDefine1=?, cDefine2=? WHERE ID=?");
            VoucherSoContractDown.CheckCap(VoucherSoContractDown.Cap, "x");
            try
            {
                VoucherSoContractDown.CheckCap(VoucherSoContractDown.Cap + 1, "x");
            }
            catch (BridgeException ex)
            {
                Expect("cap", ex.Status == 409 && ex.Code == "too_many_downstream");
                return;
            }
            throw new InvalidOperationException("so_contract pair cap not refused");
        }

        static void Plan(string name, SoContractAsk ask, SoContractSnap snap, bool changed, bool set1, bool set2)
        {
            SoContractPlan plan = VoucherSoContract.Decide(ask, snap);
            Expect(name, plan.Changed == changed && plan.SetContract == set1 && plan.SetCustomer == set2);
        }

        static void Mismatch(string name, SoContractAsk ask, SoContractSnap snap, string cur1, string cur2)
        {
            try
            {
                VoucherSoContract.Decide(ask, snap);
            }
            catch (BridgeException ex)
            {
                Dictionary<string, object> current = ex.Detail == null ? null : ex.Detail["current"] as Dictionary<string, object>;
                Expect(name, ex.Status == 409 && ex.Code == "state_mismatch" && current != null
                    && (string)current[K1] == cur1 && (string)current[K2] == cur2);
                return;
            }
            throw new InvalidOperationException("so_contract " + name + " not refused");
        }

        static void Down(string name, SoContractAsk ask, SoDownDoc doc, string result, string reason)
        {
            VoucherSoContractPair.DecideDown(ask, doc, "张三");
            Expect(name + " (" + doc.Result + "/" + doc.Reason + ")", doc.Result == result && doc.Reason == reason);
        }

        static SoContractAsk Ask(string no, string internalNo, string expect1, string expect2)
        {
            SoContractAsk ask = new SoContractAsk();
            ask.Id = 7;
            ask.Pair = true;
            ask.ContractNo = no;
            ask.CustomerNo = internalNo;
            ask.Expect1 = expect1;
            ask.Expect2 = expect2;
            return ask;
        }

        static SoContractSnap Snap(string contract, string internalNo)
        {
            SoContractSnap snap = new SoContractSnap();
            snap.Code = "SO1";
            snap.Contract = contract;
            snap.Customer = internalNo;
            snap.Locker = "";
            snap.Ts = "100";
            return snap;
        }

        static SoDownDoc Doc(string contract, string internalNo, string locker, params SoDownSource[] sources)
        {
            SoDownDoc doc = new SoDownDoc();
            doc.Kind = VoucherSoContractDown.Kinds[0];
            doc.Id = 11;
            doc.Code = "FH0001";
            doc.Contract = contract;
            doc.Internal = internalNo;
            doc.Locker = locker;
            doc.Ts = "200";
            doc.Sources.AddRange(sources);
            return doc;
        }

        static SoDownSource Src(int id, string contract, string internalNo)
        {
            SoDownSource s = new SoDownSource();
            s.Id = id;
            s.Contract = contract;
            s.Internal = internalNo;
            return s;
        }

        static Dictionary<string, object> Exp(object contract, object internalNo)
        {
            return Map(K1, contract, K2, internalNo);
        }

        static Dictionary<string, object> Body(object no, object internalNo, object expect)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["type"] = "sale_order";
            body["id"] = 7;
            body[VoucherSoContract.ModeKey] = VoucherSoContractPair.Mode;
            if (no != null)
            {
                body[K1] = no;
            }
            if (internalNo != null)
            {
                body[K2] = internalNo;
            }
            if (expect != null)
            {
                body["expect"] = expect;
            }
            return body;
        }

        static Dictionary<string, object> With(Dictionary<string, object> body, string key, object value)
        {
            body[key] = value;
            return body;
        }

        static Dictionary<string, object> Copy(Dictionary<string, object> body)
        {
            return new Dictionary<string, object>(body);
        }

        static Dictionary<string, object> Map(params object[] pairs)
        {
            return VoucherHeadFixSelfTest.Map(pairs);
        }

        static void Bad(string name, Dictionary<string, object> body)
        {
            try
            {
                VoucherSoContract.Parse(body);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request");
                return;
            }
            throw new InvalidOperationException("so_contract pair " + name);
        }

        static void Eq(string name, object got, object want)
        {
            Expect(name + " (" + got + " != " + want + ")", object.Equals(got, want));
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("so_contract pair " + name);
            }
        }
    }
}
