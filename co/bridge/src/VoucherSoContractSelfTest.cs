using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的销售订单合同号回写部分：请求解析与拒绝、类型闸门、空值等价、写什么的纯判断（空 / 「无」/ 已是目标 /
    // 挪进客户合同号 / 不覆盖）、expect 不符的 detail.current、锁定人、长度、UPDATE 片段、回读核对、响应形状，
    // 以及写分类 / 写闸门 / 预演模式 / 权限规则 / 单据锁 / 只读例外表 / 策略词表的登记。不连库。
    internal static class VoucherSoContractSelfTest
    {
        const string K1 = VoucherSoContract.ContractKey;
        const string K2 = VoucherSoContract.CustomerKey;

        public static void Run()
        {
            CheckParse();
            CheckParseRejects();
            CheckGuard();
            CheckSame();
            CheckDecide();
            CheckDecideRefuse();
            CheckLocker();
            CheckLength();
            CheckSetClause();
            CheckReadBack();
            CheckWiring();
        }

        static void CheckParse()
        {
            SoContractAsk ask = VoucherSoContract.Parse(Body("HT202611001", null, Exp(null, "")));
            Eq("parse no", ask.ContractNo, "HT202611001");
            Expect("parse no customer", ask.CustomerNo == null);
            Eq("parse expect null", ask.Expect1, "");
            Eq("parse expect empty", ask.Expect2, "");
            ask = VoucherSoContract.Parse(Body("HT202611001", "KH-088", Exp(" KH-088 ", "无")));
            Eq("parse customer", ask.CustomerNo, "KH-088");
            Eq("parse expect raw kept", ask.Expect1, " KH-088 ");
            Eq("parse expect placeholder", ask.Expect2, "无");
            Eq("parse long ok", VoucherSoContract.Parse(Body(new string('A', 120), null, Exp("", ""))).ContractNo.Length, 120);
        }

        static void CheckParseRejects()
        {
            Bad("missing no", Body(null, null, Exp("", "")));
            Bad("blank no", Body("  ", null, Exp("", "")));
            Bad("placeholder no", Body("无", null, Exp("", "")));
            Bad("leading blank", Body(" HT1", null, Exp("", "")));
            Bad("control", Body("HT\t1", null, Exp("", "")));
            Bad("too long", Body(new string('A', 121), null, Exp("", "")));
            Bad("not string", Body(12, null, Exp("", "")));
            Bad("customer placeholder", Body("HT1", "无", Exp("", "")));
            Bad("customer blank", Body("HT1", "", Exp("", "")));
            Bad("customer not string", Body("HT1", 5, Exp("", "")));
            Bad("no expect", Body("HT1", null, null));
            Bad("expect not object", Body("HT1", null, "x"));
            Bad("expect missing key", Body("HT1", null, Map(K1, "")));
            Bad("expect extra key", Body("HT1", null, Map(K1, "", K2, "", "cdefine3", "")));
            Bad("expect number", Body("HT1", null, Exp(3, "")));
            Bad("expect control", Body("HT1", null, Exp("a\nb", "")));
            Bad("expect too long", Body("HT1", null, Exp(new string('A', 121), "")));
        }

        static void CheckGuard()
        {
            VoucherSoContract.GuardKind(Kinds.Find("sale_order"));
            BadKind("guard dispatch", Kinds.Find("dispatch"));
            BadKind("guard purchase", Kinds.Find("purchase_order"));
            BadKind("guard null", null);
        }

        static void CheckSame()
        {
            Expect("blank null", VoucherSoContract.Blank(null));
            Expect("blank spaces", VoucherSoContract.Blank("   "));
            Expect("blank placeholder", VoucherSoContract.Blank(" 无 "));
            Expect("blank value", !VoucherSoContract.Blank("HT1"));
            Expect("same blanks", VoucherSoContract.Same(null, "无") && VoucherSoContract.Same("", " "));
            Expect("same case trim", VoucherSoContract.Same(" ht202611001 ", "HT202611001"));
            Expect("same differs", !VoucherSoContract.Same("HT1", "HT2") && !VoucherSoContract.Same("", "HT1"));
        }

        static void CheckDecide()
        {
            Plan("blank null", Ask("HT1", null, "", ""), Snap(null, null), true, true, false);
            Plan("blank placeholder", Ask("HT1", null, "无", ""), Snap("无", ""), true, true, false);
            Plan("blank spaces", Ask("HT1", null, "", ""), Snap("  ", null), true, true, false);
            Plan("already target", Ask("HT1", null, "ht1 ", ""), Snap(" ht1 ", null), false, false, false);
            Plan("already both", Ask("HT1", "KH-1", "HT1", "KH-1"), Snap("HT1", "kh-1"), false, false, false);
            Plan("move customer", Ask("HT1", "KH-1", "KH-1", ""), Snap("KH-1", null), true, true, true);
            Plan("customer only", Ask("HT1", "KH-1", "HT1", ""), Snap("HT1", "无"), true, false, true);
            Plan("customer into blank", Ask("HT1", "KH-1", "", ""), Snap(null, null), true, true, true);
        }

        static void CheckDecideRefuse()
        {
            Mismatch("expect differs", Ask("HT1", null, "", ""), Snap("HT9", null), "HT9", null);
            Mismatch("expect customer differs", Ask("HT1", null, "", ""), Snap("", "KH-9"), "", "KH-9");
            Mismatch("other number", Ask("HT1", null, "HT9", ""), Snap("HT9", null), "HT9", null);
            Mismatch("move needs same value", Ask("HT1", "KH-2", "KH-1", ""), Snap("KH-1", null), "KH-1", null);
            Mismatch("customer not blank", Ask("HT1", "KH-1", "KH-1", "KH-0"), Snap("KH-1", "KH-0"), "KH-1", "KH-0");
            Mismatch("customer not blank target set", Ask("HT1", "KH-1", "HT1", "KH-0"), Snap("HT1", "KH-0"), "HT1", "KH-0");
        }

        static void CheckLocker()
        {
            VoucherSoContract.CheckLocker("", "张三");
            VoucherSoContract.CheckLocker(" 张三 ", "张三");
            try
            {
                VoucherSoContract.CheckLocker("李四", "张三");
            }
            catch (BridgeException ex)
            {
                Expect("locked", ex.Status == 409 && ex.Code == "state_mismatch"
                    && (string)ex.Detail["reason"] == "locked" && (string)ex.Detail["locker"] == "李四");
                return;
            }
            throw new InvalidOperationException("so_contract locker not refused");
        }

        static void CheckLength()
        {
            VoucherSoContract.CheckLength("HT202611001", 20, K1);
            VoucherSoContract.CheckLength(new string('A', 200), 0, K1);
            VoucherSoContract.CheckLength(null, 5, K2);
            try
            {
                VoucherSoContract.CheckLength("HT202611001", 10, K1);
            }
            catch (BridgeException ex)
            {
                Expect("length", ex.Status == 400 && ex.Code == "bad_request" && ex.Field == K1);
                Eq("limit col", VoucherSoContractSql.Limit(20, 0), 20);
                Eq("limit def smaller", VoucherSoContractSql.Limit(20, 12), 12);
                Eq("limit def larger", VoucherSoContractSql.Limit(20, 40), 20);
                Eq("limit def only", VoucherSoContractSql.Limit(0, 30), 30);
                Eq("limit none", VoucherSoContractSql.Limit(0, 0), 0);
                return;
            }
            throw new InvalidOperationException("so_contract length not refused");
        }

        static void CheckSetClause()
        {
            SoContractPlan both = new SoContractPlan();
            both.SetContract = true;
            both.SetCustomer = true;
            List<string> names = new List<string>();
            string all = VoucherSoContractSql.SetClause(both,
                new string[] { "cDefine1", "cDefine2", "cmodifier", "dModDate", "dmodifysystime" }, names);
            Eq("set all", all, "cDefine1=?, cDefine2=?, cModifier=?, dModDate=CONVERT(datetime, CONVERT(date, ?, 23)),"
                + " dmodifysystime=GETDATE()");
            Eq("set all names", string.Join(",", names.ToArray()), "c1,c2,who,date");
            SoContractPlan one = new SoContractPlan();
            one.SetCustomer = true;
            names.Clear();
            Eq("set customer only", VoucherSoContractSql.SetClause(one, new string[] { "cDefine1", "cDefine2" }, names),
                "cDefine2=?");
            Eq("set customer names", string.Join(",", names.ToArray()), "c2");
        }

        static void CheckReadBack()
        {
            Dictionary<string, object> head = Map("code", "SO1", "c2", "", "locker", "", "ts", "123");
            SoContractSnap snap = VoucherSoContractSql.Snap(head);
            Expect("snap null kept", snap.Contract == null && snap.Customer == "");
            Eq("snap ts", snap.Ts, "123");
            SoContractAsk ask = Ask("HT1", "KH-1", "", "");
            ask.Id = 7;
            SoContractPlan plan = VoucherSoContract.Decide(ask, snap);
            Expect("written", VoucherSoContractSql.Written(ask, plan, Snap("HT1 ", "KH-1")));
            Expect("written differs", !VoucherSoContractSql.Written(ask, plan, Snap("HT1", null)));
            Expect("written case", !VoucherSoContractSql.Written(ask, plan, Snap("ht1", "KH-1")));
            Dictionary<string, object> body = VoucherSoContract.Body(ask, Snap("HT1", "KH-1"), true);
            Dictionary<string, object> current = (Dictionary<string, object>)body["current"];
            Expect("body head", (bool)body["ok"] && (string)body["type"] == "sale_order" && (int)body["id"] == 7);
            Expect("body current", (bool)body["changed"] && (string)current[K1] == "HT1" && (string)current[K2] == "KH-1");
            Expect("body keys", body.ContainsKey("ufts") && body.ContainsKey("code") && !body.ContainsKey("dry_run"));
            Dictionary<string, object> dry = VoucherSoContract.DryBody(ask, snap, true);
            Expect("dry body", (bool)dry["dry_run"] && (string)dry["mode"] == DryRunModes.Validate
                && ((Dictionary<string, object>)dry["current"])[K1] == null);
        }

        static void CheckWiring()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["type"] = "sale_order";
            WriteRequestInfo info = WriteClass.Of(VoucherSoContract.Path, body);
            Expect("wiring class", info != null && info.Type == "sale_order" && info.Op == "so_contract");
            Expect("wiring op listed", WriteClass.IsOp("so_contract")
                && Array.IndexOf(WritePolicySnapshot.OpNames, "so_contract") >= 0);
            Expect("wiring write gate", WriteGate.IsWrite(VoucherSoContract.Path));
            Expect("wiring dry accepts", DryRunReq.Accepts(VoucherSoContract.Path));
            Eq("wiring dry mode", DryRunModes.Mode("vouchers/so_contract", Kinds.Find("sale_order"), "so_contract", ""), "validate");
            Eq("wiring dry other", DryRunModes.Mode("vouchers/so_contract", Kinds.Find("dispatch"), "so_contract", ""), "refuse");
            PermRule rule = PermRegistry.ForKey(VoucherSoContract.RuleKey);
            Expect("wiring perm rule", rule != null && rule.Objs.Length == 4);
            Expect("wiring perm id", rule.Auths.Length == 1 && rule.Auths[0] == PermRegistry.SoChange);
            WorkItem item = new WorkItem();
            item.Path = VoucherSoContract.Path;
            item.Type = Kinds.Find("sale_order");
            item.HasId = true;
            item.Id = 7;
            WorkItem update = new WorkItem();
            update.Path = "/u8co/v1/vouchers/update";
            update.Type = item.Type;
            update.HasId = true;
            update.Id = 7;
            Expect("wiring same lock as update", Array.IndexOf(DocLocks.KeysOf(item), "sale_order:7") >= 0
                && Array.IndexOf(DocLocks.KeysOf(update), "sale_order:7") >= 0);
            Expect("wiring spec", VoucherSoContract.Spec[0] == VoucherSoContract.Path && VoucherSoContract.Spec.Length == 6);
            CheckReadOnlyTable();
        }

        static void CheckReadOnlyTable()
        {
            int at = Array.IndexOf(ConfigRulesReadOnly.VoucherOps, VoucherSoContract.Op);
            Expect("wiring readonly table", at >= 0 && ConfigRulesReadOnly.VoucherPaths[at] == VoucherSoContract.Path
                && ConfigRulesReadOnly.VoucherKinds[at] == VoucherSoContract.Kind);
            Expect("wiring readonly aligned", ConfigRulesReadOnly.VoucherPaths.Length == ConfigRulesReadOnly.VoucherOps.Length
                && ConfigRulesReadOnly.VoucherKinds.Length == ConfigRulesReadOnly.VoucherOps.Length);
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

        static SoContractAsk Ask(string no, string customer, string expect1, string expect2)
        {
            SoContractAsk ask = new SoContractAsk();
            ask.ContractNo = no;
            ask.CustomerNo = customer;
            ask.Expect1 = expect1;
            ask.Expect2 = expect2;
            return ask;
        }

        static SoContractSnap Snap(string contract, string customer)
        {
            SoContractSnap snap = new SoContractSnap();
            snap.Code = "SO1";
            snap.Contract = contract;
            snap.Customer = customer;
            snap.Locker = "";
            snap.Ts = "100";
            return snap;
        }

        static Dictionary<string, object> Exp(object contract, object customer)
        {
            return Map(K1, contract, K2, customer);
        }

        static Dictionary<string, object> Body(object no, object customer, object expect)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["type"] = "sale_order";
            body["id"] = 7;
            if (no != null)
            {
                body[K1] = no;
            }
            if (customer != null)
            {
                body[K2] = customer;
            }
            if (expect != null)
            {
                body["expect"] = expect;
            }
            return body;
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
            throw new InvalidOperationException("so_contract " + name);
        }

        static void BadKind(string name, VoucherKind kind)
        {
            try
            {
                VoucherSoContract.GuardKind(kind);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request");
                return;
            }
            throw new InvalidOperationException("so_contract " + name);
        }

        static void Eq(string name, object got, object want)
        {
            Expect(name + " (" + got + " != " + want + ")", object.Equals(got, want));
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("so_contract " + name);
            }
        }
    }
}
