using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的受控 SQL 档案写入部分：列校验（ArcCol）、存货 Inventory_Sub 扩展标签（ArcInvSub）、收付款协议（ArcAgreement）、
    // 自定义项档案和客户存货对照的修改（ArcPairEdit）。只测纯逻辑，不连库、不读 U8 目录。
    internal static class ArcSqlWriteSelfTest
    {
        public static void Run()
        {
            CheckCols();
            CheckInventory();
            CheckAgreement();
            CheckPairEdit();
            CheckWiring();
        }

        static void CheckCols()
        {
            CheckClean();
            CheckRanges();
            CheckSql();
        }

        // 规范写法。
        static void CheckClean()
        {
            Expect("col bit text", ArcCol.Of("b", 'b', false).Clean(" TRUE ") == "1");
            Expect("col int sign", ArcCol.Of("i", 'i', false).Clean("+05") == "5");
            Expect("col float", ArcCol.Of("f", 'f', true).Clean("0.50") == "0.50");
            Expect("col float empty", ArcCol.Of("f", 'f', true).Clean("") == "");
            Expect("col date", ArcCol.Of("d", 'd', true).Clean("2026-02-28") == "2026-02-28");
            Expect("col float 15", ArcCol.Of("f", 'f', false).Clean("0.123456789012345") == "0.123456789012345");
        }

        // 拒绝的取值。
        static void CheckRanges()
        {
            ColRejects("col short range", ArcCol.Of("s", 's', false), "32768");
            ColRejects("col int range", ArcCol.Of("i", 'i', false), "2147483648");
            ColRejects("col float exp", ArcCol.Of("f", 'f', false), "1e3");
            ColRejects("col bit two", ArcCol.Of("b", 'b', false), "2");
            ColRejects("col date bad", ArcCol.Of("d", 'd', false), "2026-02-30");
            ColRejects("col decimal digits", ArcCol.Of("n", 'n', false), "1234567890123456789");
            ColRejects("col float digits", ArcCol.Of("f", 'f', false), "0.1234567890123456");
            ColRejects("col not null", ArcCol.Text("t", 5, false), "  ");
            ColRejects("col text long", ArcCol.Text("t", 5, true), "123456");
        }

        // 缺省值、SQL 参数、值表达式、回读比较。
        static void CheckSql()
        {
            ArcCol memo = ArcCol.Memo("m", 5);
            ArcCol text = ArcCol.Text("t", 5, true);
            Expect("col memo", memo.Default() == "" && (string)memo.Param("") == "" && memo.Clean("") == "");
            Expect("col defaults", text.Default() == null && text.Param("") == null && ArcCol.Of("i", 'i', false).Default() == "0");
            Expect("col date default", ArcCol.Of("d", 'd', true).Default() == null);
            Expect("col param int", (int)ArcCol.Of("i", 'i', false).Param("7") == 7);
            Expect("col param null", ArcCol.Of("i", 'i', true).Param("") == null);
            Expect("col expr num", ArcCol.Of("n", 'n', false).Expr().IndexOf("decimal(18,0)", StringComparison.Ordinal) > 0);
            Expect("col expr", ArcCol.Of("d", 'd', false).Expr() == "CAST(CONVERT(date, ?, 23) AS datetime)" && text.Expr() == "?");
            Expect("col same num", ArcCol.Of("f", 'f', true).Same("0.5", "0.50") && !ArcCol.Of("f", 'f', true).Same("0.5", "0.6"));
            Expect("col same date", ArcCol.Of("d", 'd', true).Same("2026-02-28", "2026-02-28 00:00:00"));
            Expect("col same empty", ArcCol.Of("d", 'd', true).Same("", null));
        }

        static void CheckInventory()
        {
            CheckVerify(CheckInventoryMap());
            CheckInventoryValues();
        }

        // 存货字段表：扩展标签并入、模板和修改跳过、只读列。
        static ArcMap CheckInventoryMap()
        {
            ArcKind k = ArcKind.Find("inventory");
            Expect("inv extra pairs", k.Extra.Length == 2 * 99 && k.Extra[0] == k.Extra[1]);
            ArcMap map = ArcMap.Fixed(new string[] { "fbuyexcess", "fBuyExcess" });
            map.Append(k.Extra);
            Expect("inv map", map.Canon("FInvOutUpLimit") == "finvoutuplimit" && map.Column("iwarrantyperiod") == "iwarrantyperiod");
            Expect("inv map tags", map.Canon("bbatchproperty10") == "bbatchproperty10" && map.Tags.Count == 100);
            Expect("inv own tags", k.TplSkipped("finvoutuplimit") && k.UpdSkipped("bbondedinv") && !k.UpdSkipped("fbuyexcess"));
            Expect("inv read only", k.Blocked("fMaterialCost") && k.Blocked("dInvCreateDatetime") && !k.Blocked("idrawtype"));
            Expect("inv read only map", map.Canon("fMaterialCost") == "fMaterialCost" && k.UpdSkipped("bX64") && k.TplSkipped("bX64"));
            return map;
        }

        static void CheckInventoryValues()
        {
            ArcReq req = Req("inventory", "update", "binbyprocheck", "False", "fbuyexcess", " 3.5 ", "idrawtype", "2");
            ArcInvSub.Check(req);
            Expect("inv clean", req.Fields.Get("binbyprocheck") == "0" && req.Fields.Get("fbuyexcess") == "3.5");
            Expect("inv clean int", req.Fields.Get("idrawtype") == "2");
            InvRejects("inv bit", "binbyprocheck", "2");
            InvRejects("inv smallint", "idrawtype", "70000");
            InvRejects("inv float", "finvoutuplimit", "abc");
            InvRejects("inv mapped bit", "bPrjMat", "yes");
            InvRejects("inv empty", "iwarrantyperiod", "");
            ArcInvSub.Check(Req("customer", "update", "binbyprocheck", "x"));
        }

        // 落库核对：U8 没写的 Sub 列（如批次属性被 bBatchCreate=0 压成 0）列进 502 u8_fields_ignored 的 detail.fields。
        static void CheckVerify(ArcMap map)
        {
            ArcReq req = Req("inventory", "create", "binbyprocheck", "0", "bbatchproperty1", "1", "finvoutuplimit", "12.5");
            req.Map = map;
            Dictionary<string, string> row = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            row["binbyprocheck"] = "0";
            row["bbatchproperty1"] = "0";
            row["finvoutuplimit"] = "12.5";
            BridgeException bad = ArcInvSub.Verify(req, row);
            Dictionary<string, object> fields = bad == null || bad.Detail == null ? null : (Dictionary<string, object>)bad.Detail["fields"];
            Expect("verify ignored", bad != null && bad.Status == 502 && bad.Code == ArcInvSub.FieldsIgnored && fields != null
                && fields.Count == 1 && fields.ContainsKey("bbatchproperty1"));
            Dictionary<string, object> pair = (Dictionary<string, object>)fields["bbatchproperty1"];
            Expect("verify pair", (string)pair["sent"] == "1" && (string)pair["stored"] == "0");
            row["bbatchproperty1"] = "1";
            Expect("verify ok", ArcInvSub.Verify(req, row) == null);
            row["finvoutuplimit"] = "12.50";
            Expect("verify float", ArcInvSub.Verify(req, row) == null);
            Expect("verify other kind", ArcInvSub.Verify(Req("customer", "update", "x", "1"), row) == null);
        }

        static void CheckAgreement()
        {
            ArcKind k = ArcKind.Find(ArcAgreement.Name);
            Expect("agreement writable", !k.ReadOnly && k.RoRead && k.NameTag == "cName" && k.SqlMap != null);
            Expect("agreement tags", k.SqlMap.Canon("dblzqnum") == "dblZQNum" && k.SqlMap.Canon("cMaker") == null
                && k.SqlMap.Canon("dtDate") == null && k.SqlMap.Canon("ufts") == null && k.SqlMap.Tags.Count == 16);
            ArcAgreement.Check(Req(ArcAgreement.Name, "create", "cName", "协议A", "iLZYJ", "0", "dblZQNum", "30", "dEDate", ""));
            AgreementRejects("agreement name empty", Req(ArcAgreement.Name, "update", "cName", " "));
            AgreementRejects("agreement int", Req(ArcAgreement.Name, "update", "iPLNum", "3.5"));
            AgreementRejects("agreement date", Req(ArcAgreement.Name, "update", "dEDate", "2026/11/24"));
            AgreementRejects("agreement maker", Req(ArcAgreement.Name, "update", "cMaker", "x"));
            AgreementRejects("agreement clear term", Req(ArcAgreement.Name, "update", "iZQ", ""));
            ArcReq withTemplate = Req(ArcAgreement.Name, "create", "cName", "x");
            withTemplate.Template = "001";
            AgreementRejects("agreement template", withTemplate);
        }

        static void CheckPairEdit()
        {
            Expect("pair edit kinds", ArcPairEdit.Is(ArcKind.Find("user_define")) && ArcPairEdit.Is(ArcKind.Find("customer_inventory"))
                && !ArcPairEdit.Is(ArcKind.Find("customer_address")));
            Expect("pair updatable", !ArcKind.Find("user_define").NoUpdate && !ArcKind.Find("customer_inventory").NoUpdate);
            ArcReq define = Req("user_define", "update", "alias", "KD", "barcode", "");
            ArcPairEdit.Check(define);
            ArcReq contra = Req("customer_inventory", "update", "boutinvdt", "true", "fdtrate", "0.5", "ccusinvname", "客户叫法");
            ArcPairEdit.Check(contra);
            Expect("contra clean", contra.Fields.Get("boutinvdt") == "1");
            PairRejects("define long alias", Req("user_define", "update", "alias", new string('a', 21)));
            PairRejects("define key tag", Req("user_define", "update", "value", "x"));
            PairRejects("contra bit empty", Req("customer_inventory", "update", "boutinvdt", ""));
            PairRejects("contra name empty", Req("customer_inventory", "update", "ccusinvname", " "));
            PairRejects("contra short", Req("customer_inventory", "update", "iteststyle", "40000"));
            ArcPairEdit.Check(Req("user_define", "create", "alias", new string('a', 21)));
            string unique = ArcPairEdit.UniqueSql("Calias");
            Expect("alias unique sql", unique.IndexOf("WITH (UPDLOCK, HOLDLOCK) WHERE cID=? AND Calias=? AND cValue<>?", StringComparison.Ordinal) > 0);
        }

        // 写权限登记（AS60202、不读 U8 目录）和预演模式（受控 SQL 回滚，EAI 停在调用前）。
        static void CheckWiring()
        {
            string[] ops = new string[] { "create", "update", "delete" };
            for (int i = 0; i < ops.Length; i++)
            {
                PermRule rule = PermRegistry.ForKey(ArcGuard.RuleKey(ArcAgreement.Name, ops[i]));
                Expect("agreement rule " + ops[i], rule != null && rule.Auths.Length == 1 && rule.Auths[0] == "AS60202");
                Expect("agreement dry " + ops[i], DryRunModes.Lookup("archives/" + ops[i], ArcAgreement.Name, "archive_" + ops[i], "") == "rollback");
            }
            Expect("define update dry", DryRunModes.Lookup("archives/update", "user_define", "archive_update", "") == "rollback");
            Expect("contra update dry", DryRunModes.Lookup("archives/update", "customer_inventory", "archive_update", "") == "rollback");
            Expect("define create dry", DryRunModes.Lookup("archives/create", "user_define", "archive_create", "") == "validate");
            Expect("define update rule", PermRegistry.ForKey(ArcGuard.RuleKey("user_define", "update")) != null);
            Expect("contra update rule", PermRegistry.ForKey(ArcGuard.RuleKey("customer_inventory", "update")) != null);
        }

        static void ColRejects(string name, ArcCol col, string value)
        {
            Rejects(name, delegate { col.Clean(value); });
        }

        static void InvRejects(string name, string tag, string value)
        {
            Rejects(name, delegate { ArcInvSub.Check(Req("inventory", "update", tag, value)); });
        }

        static void AgreementRejects(string name, ArcReq req)
        {
            Rejects(name, delegate { ArcAgreement.Check(req); });
        }

        static void PairRejects(string name, ArcReq req)
        {
            Rejects(name, delegate { ArcPairEdit.Check(req); });
        }

        static void Rejects(string name, Action act)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                Expect(name + " status", ex.Status == 400);
                return;
            }
            throw new InvalidOperationException(name);
        }

        // 不经 ArcReq.Parse（它要读 U8 目录的 RsXml）：手工组请求，fields 取标签、值成对。
        static ArcReq Req(string archive, string op, params string[] pairs)
        {
            ArcReq req = new ArcReq();
            req.Op = op;
            req.Kind = ArcKind.Find(archive);
            req.Code = "T9";
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                req.Fields.Put(pairs[i], pairs[i + 1]);
            }
            return req;
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
