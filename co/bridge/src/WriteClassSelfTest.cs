using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的写入分类部分（WriteClass、WriteClassGate）：Dispatch 路由表里每条写路由都分到合法的（type, op），
    // 行数、金额、预演的取法，第二级写入的登记表（含总开关与测试账套名单的拒绝顺序），以及登录前 / 入队后两处检查
    // （策略关闭与冻结）。不连库、不建 COM。
    // 由 SelfTest.RunWritePolicy 调用。
    internal static class WriteClassSelfTest
    {
        // TestAccountGate.Require 的调用点登记表（路由、type、拒绝原文）。Require 不带调用点编号，没法自动枚举：
        // 新增一处 TestAccountGate.Require 时在这里加一行（文档：limitations.md「写入分级」）。
        // 期初、月末结账、存货核算整族都是第二级写入，自检另外核对这三族的每条写路由都在表里。
        // 采购手工结算升为第一级，已从表里去掉；汇兑损益（应收、应付）仍是第二级。
        internal static readonly string[][] TestOnlySites = new string[][]
        {
            new string[] { OpeningPostReq.Path, "openings", TestAccountGate.OpeningPostText },
            new string[] { OpeningsArapReq.Path, "openings", TestAccountGate.OpeningsArapText },
            new string[] { PeriodCloseReq.Path, "periods", PeriodCloseReq.TestOnly },
            new string[] { IaReq.PostPath, "ia", IaReq.TestOnly },
            new string[] { IaReq.PeriodEndPath, "ia", IaReq.TestOnly },
            new string[] { "/u8co/v1/vouchers/create", StockOpening.KindName, StockOpening.TestOnlyText },
            new string[] { "/u8co/v1/vouchers/delete", StockOpening.KindName, StockOpening.TestOnlyText },
            new string[] { "/u8co/v1/vouchers/verify", StockOpening.KindName, StockOpening.TestOnlyText },
            new string[] { ArapBadReq.Path, "arap", ArapBadReq.TestOnly },
            new string[] { ArapExGainReq.Path, "arap", ArapExGainReq.TestOnly },
            new string[] { ArapExGainReq.CancelPath, "arap", ArapExGainReq.TestOnly },
            new string[] { ArapProcVoucherReq.Path, "arap", ArapProcVoucherReq.TestOnly },
            new string[] { ArapProcVoucherReq.Path, "arap", ArapProcVoucherReq.BadTestOnly },
            new string[] { ArapProcVoucherReq.Path, "arap", ArapProcVoucherReq.ApNotesTestOnly },
            // 取消制单里的处理凭证（ArapProcVoucherDrop、ArapProcVoucherBad）。
            new string[] { Requests.ArapVoucherDropPath, "arap", ArapProcVoucherReq.TestOnly },
            new string[] { Requests.ArapVoucherDropPath, "arap", ArapProcVoucherReq.BadTestOnly },
            new string[] { Requests.ArapVoucherDropPath, "arap", ArapProcVoucherReq.ApNotesTestOnly },
            new string[] { ArapProcCancelReq.Path, "arap", ArapProcCancelBad.TestOnly },
            // 只限应付票据；应收票据是第一级。
            new string[] { NotesRegReq.CreatePath, "notes", NotesRegReq.ApTestOnly },
            new string[] { NotesRegReq.DeletePath, "notes", NotesRegReq.ApTestOnly },
            new string[] { NotesProcReq.Path, "notes", NotesProcReq.ApTestOnly },
            // 取消记账（登录前 RequestsP4、入队后 GlUnpost）。
            new string[] { GlUnpostReq.Path, "gl", GlUnpostReq.TestOnly },
            // 期间损益结转、自定义转账（登录前 RequestsGlTransfer、入队后 GlTransfer）。
            new string[] { GlTransferReq.PnlPath, "gl", GlTransferReq.PnlTestOnly },
            new string[] { GlTransferReq.CustomPath, "gl", GlTransferReq.CustomTestOnly }
        };

        public static void Run()
        {
            CheckCoverage();
            CheckVouchers();
            CheckFamilies();
            CheckLines();
            CheckAmounts();
            CheckTestOnlySites();
            TestAccountGateSelfTest.Run();
            CheckPromoted();
            CheckGateOff();
            CheckGateFrozen();
        }

        // Dispatch 路由表的每条写路由都登记了，登记的都是写路由；规则里的每个 op 都在词表里。
        static void CheckCoverage()
        {
            List<string> routes = DispatchRoutes.Paths();
            int writes = 0;
            for (int i = 0; i < routes.Count; i++)
            {
                if (!WriteGate.IsWrite(routes[i]))
                {
                    Expect("read not classified " + routes[i], WriteClass.Of(routes[i], Body("sale_order", "verify")) == null);
                    continue;
                }
                writes++;
                WriteRequestInfo info = WriteClass.Of(routes[i], Body("sale_order", "verify"));
                Expect("write classified " + routes[i], info != null && !string.IsNullOrEmpty(info.Type) && WriteClass.IsOp(info.Op));
            }
            foreach (string path in WriteClass.Paths())
            {
                Expect("rule is write " + path, WriteGate.IsWrite(path) && routes.Contains(path));
                List<string> ops = WriteClass.RuleOps(path);
                for (int i = 0; i < ops.Count; i++)
                {
                    Expect("rule op " + path + " " + ops[i], WriteClass.IsOp(ops[i]));
                }
            }
            Expect("rule count", writes == WriteClass.Paths().Count);
        }

        static void CheckVouchers()
        {
            Is("create", V("create"), Body("sale_order", null), "sale_order", "create");
            Is("verify", V("verify"), Body("purchase_order", "unverify"), "purchase_order", "unverify");
            Is("arap verify", V("verify"), Body("purchase_invoice", "arap_verify"), "purchase_invoice", "verify");
            Is("verify no action", V("verify"), Body("purchase_order", null), "purchase_order", "other");
            Is("open", V("close"), Body("sale_order", "open"), "sale_order", "open");
            Is("unlock", Requests.LockPath, Body("purchase_order", "unlock"), "purchase_order", "unlock");
            Is("generate", V("generate"), Body("sale_out", null), "sale_out", "generate");
            Is("workflow", "/u8co/v1/workflow/approve", Body("purchase_order", null), "purchase_order", "workflow");
            Is("legacy sale", "/u8co/v1/sale-orders/verify", Body(null, "unverify"), "sale_order", "unverify");
            Is("legacy dispatch", "/u8co/v1/dispatches/verify", Body(null, "verify"), "dispatch", "verify");
            Expect("load is read", WriteClass.Of(V("load"), Body("sale_order", null)) == null);
            Expect("null path", WriteClass.Of(null, null) == null && WriteClass.Of((WorkItem)null) == null);
        }

        static void CheckFamilies()
        {
            Is("gl post", Requests.GlRoot + "post", Body(null, null), "gl", "post");
            Is("gl sign", Requests.GlRoot + "sign", Body(null, null), "gl", "other");
            Is("archive delete", Requests.ArcRoot + "delete", Body(null, null), "archives", "delete");
            Is("writeoff", Requests.WriteoffPath, Body(null, null), "arap", "writeoff");
            Is("writeoff cancel", Requests.WriteoffCancelPath, Body(null, null), "arap", "other");
            Is("arap voucher", Requests.ArapVoucherPath, Body("sale_invoice", null), "arap", "voucher");
            Is("transfer", Requests.TransferPath, Body(null, null), "arap", "process");
            Is("notes process", NotesProcReq.Path, Body(null, null), "notes", "process");
            Is("opening unpost", OpeningPostReq.Path, Body(null, "unpost"), "openings", "other");
            Is("openings arap verify", OpeningsArapReq.Path, Body(null, "verify"), "openings", "verify");
            Is("period reopen", PeriodCloseReq.Path, Body(null, "reopen"), "periods", "open");
            Is("ia period end", IaReq.PeriodEndPath, Body(null, "run"), "ia", "close");
        }

        // 行数：新增、修改、生单数 lines；其余为 0。自动核销的 dry_run 算预演。
        static void CheckLines()
        {
            Dictionary<string, object> body = Body("sale_order", null);
            body["lines"] = new object[] { Row(null, null), Row(null, null), Row(null, null) };
            WriteRequestInfo create = WriteClass.Of(V("create"), body);
            Expect("lines create", create.Lines == 3 && !create.Amount.HasValue);
            Expect("lines verify", WriteClass.Of(V("verify"), body).Lines == 0);
            Expect("lines generate whole", WriteClass.Of(V("generate"), Body("sale_out", null)).Lines == 0);
            Dictionary<string, object> plan = Body(null, null);
            plan[DryRunReq.Field] = true;
            Expect("auto plan dry", WriteClass.DryOf(Requests.WriteoffAutoPath, plan, false));
            Expect("other not dry", !WriteClass.DryOf(Requests.WriteoffPath, plan, false));
            WorkItem item = new WorkItem();
            item.Path = "/u8co/v1/sale-orders/verify";
            item.Action = "verify";
            item.Acc = "998";
            item.Operator = "op001";
            item.DryRun = false;
            WriteRequestInfo legacy = WriteClass.Of(item);
            Expect("item legacy", legacy != null && legacy.Op == "verify" && legacy.Acc == "998" && legacy.Operator == "op001");
        }

        // 金额：收付款单、退款单的新增 / 修改取表体 iAmt 合计（大小写不论），只给原币时按表头汇率折算；其他为 null。
        static void CheckAmounts()
        {
            Dictionary<string, object> body = Body("ar_receipt", null);
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["iExchRate"] = "7";
            body["head"] = head;
            body["lines"] = new object[] { Row("iAmt", "100.50"), Row("iamt_f", 10) };
            WriteRequestInfo info = WriteClass.Of(V("create"), body);
            Expect("amount sum", info.Amount.HasValue && info.Amount.Value == 170.50m && info.Lines == 2);
            body["type"] = "ap_refund";
            body.Remove("head");
            Expect("amount refund", WriteClass.Of(V("create"), body).Amount == 110.50m);
            info = WriteClass.Of(V("update"), body);
            Expect("amount update foreign bad", info.Amount == 110.50m && info.AmountBad);
            body["lines"] = new object[] { Row("iAmt", "100.50"), Row("iamt", "-20") };
            info = WriteClass.Of(V("update"), body);
            Expect("amount update local", info.Amount == 80.50m && !info.AmountBad);
            body["lines"] = new object[] { Row("iAmt", "100.50"), Row("iamt_f", 10) };
            Expect("amount verify", !WriteClass.Of(V("verify"), body).Amount.HasValue);
            body["lines"] = new object[] { Row("iamt", "x") };
            info = WriteClass.Of(V("create"), body);
            Expect("amount bad text", !info.Amount.HasValue && info.AmountBad);
            body.Remove("lines");
            info = WriteClass.Of(V("update"), body);
            Expect("amount no lines", !info.Amount.HasValue && !info.AmountBad);
            CheckAmountForms();
        }

        // 与写入方 ArapReq 同样认指数写法（NumberStyles.Float）；空白算没给；布尔按 1 / 0；其他写法置 AmountBad。
        static void CheckAmountForms()
        {
            Dictionary<string, object> body = Body("ar_receipt", null);
            body["lines"] = new object[] { Row("iAmt", "6e6") };
            WriteRequestInfo info = WriteClass.Of(V("create"), body);
            Expect("amount exponent", info.Amount == 6000000m && !info.AmountBad);
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["iexchrate"] = "7.2e0";
            body["head"] = head;
            body["lines"] = new object[] { Row("iAmt_f", "1000000") };
            info = WriteClass.Of(V("create"), body);
            Expect("rate exponent", info.Amount == 7200000m && !info.AmountBad);
            head["iexchrate"] = "7,2";
            info = WriteClass.Of(V("create"), body);
            Expect("rate bad", info.AmountBad);
            body.Remove("head");
            body["lines"] = new object[] { Row("iamt", "  "), Row("iamt_f", null) };
            info = WriteClass.Of(V("create"), body);
            Expect("amount blank", !info.Amount.HasValue && !info.AmountBad);
            body["lines"] = new object[] { Row("iamt", "1,000") };
            Expect("amount comma bad", WriteClass.Of(V("create"), body).AmountBad);
            body["lines"] = new object[] { Row("iamt", 100), Row("iamt_f", new object[0]) };
            Expect("amount foreign bad", WriteClass.Of(V("create"), body).AmountBad);
            body["lines"] = new object[] { Row("iamt", true) };
            Expect("amount bool", WriteClass.Of(V("create"), body).Amount == 1m);
            CheckAmountGuards();
        }

        // 只差大小写的重复键（写入方取哪一个不一定）、超出 decimal 范围的金额与合计，都置 AmountBad。
        static void CheckAmountGuards()
        {
            Dictionary<string, object> body = Body("ap_payment", null);
            Dictionary<string, object> row = Row("iAmt", "");
            row["IAMT"] = "9000000";
            body["lines"] = new object[] { row };
            Expect("amount duplicate key", WriteClass.Of(V("create"), body).AmountBad);
            row = Row("iAmt_f", "100");
            row["IAMT_F"] = "100";
            body["lines"] = new object[] { row };
            Expect("foreign duplicate key", WriteClass.Of(V("create"), body).AmountBad);
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["iExchRate"] = "1";
            head["IEXCHRATE"] = "900";
            body["head"] = head;
            body["lines"] = new object[] { Row("iamt_f", "100") };
            Expect("rate duplicate key", WriteClass.Of(V("create"), body).AmountBad);
            body.Remove("head");
            body["lines"] = new object[] { Row("iamt", "7e28") };
            WriteRequestInfo info = WriteClass.Of(V("create"), body);
            Expect("amount near max", info.Amount == 70000000000000000000000000000m && !info.AmountBad);
            body["lines"] = new object[] { Row("iamt", "7e28"), Row("iamt", "7e28") };
            info = WriteClass.Of(V("create"), body);
            Expect("amount sum overflow", !info.Amount.HasValue && info.AmountBad);
            body["lines"] = new object[] { Row("iamt", "1e1000000") };
            info = WriteClass.Of(V("create"), body);
            Expect("amount huge exponent", !info.Amount.HasValue && info.AmountBad);
            head = new Dictionary<string, object>();
            head["iexchrate"] = "7e28";
            body["head"] = head;
            body["lines"] = new object[] { Row("iamt_f", "10") };
            info = WriteClass.Of(V("create"), body);
            Expect("amount rate overflow", !info.Amount.HasValue && info.AmountBad);
        }

        // 登记表的每行：路由是写路由、分类的 type 与登记一致、op 合法、原文非空；期初 / 结账 / 存货核算三族的写路由全在表里。
        static void CheckTestOnlySites()
        {
            HashSet<string> listed = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i < TestOnlySites.Length; i++)
            {
                string[] site = TestOnlySites[i];
                bool voucher = site[0].StartsWith("/u8co/v1/vouchers/", StringComparison.Ordinal);
                WriteRequestInfo info = WriteClass.Of(site[0], Body(voucher ? site[1] : null, null));
                string name = "test-only site " + site[0] + " " + site[1];
                Expect(name, info != null && info.Type == site[1] && WriteClass.IsOp(info.Op) && !string.IsNullOrEmpty(site[2]));
                // 每个调用点的原文：开关关闭（缺省）时 feature_disabled，打开后才轮到测试账套名单。
                TestAccountGateSelfTest.Check(name, site[2]);
                listed.Add(site[0]);
            }
            foreach (string path in WriteClass.Paths())
            {
                string type = WriteClass.Of(path, Body("sale_order", null)).Type;
                if (type == "openings" || type == "periods" || type == "ia")
                {
                    Expect("test-only family listed " + path, listed.Contains(path));
                }
            }
        }

        // 采购手工结算升为第一级，不在登记表里；汇兑损益（应收、应付的新增、取消、制单）和坏账制单仍是第二级：
        // 没有任务（按开关关闭处理）时 403 feature_disabled，开关打开、账套不在测试账套名单里时 403 test_account_only。
        static void CheckPromoted()
        {
            for (int i = 0; i < TestOnlySites.Length; i++)
            {
                Expect("settle not listed " + TestOnlySites[i][0], TestOnlySites[i][1] != PuSettleRead.KindName);
            }
            CheckSecondTier(null, TestAccountGate.FeatureCode);
            WorkItem item = Item("997");
            item.Config.EnableReplicatedWrites = true;
            item.Config.TestAccounts = new string[] { "998" };
            CheckSecondTier(item, TestAccountGate.Code);
        }

        static void CheckSecondTier(WorkItem item, string code)
        {
            Expect("exchange gain still test-only " + code, Refused(delegate { ArapExGainReq.TestGate(item); }) == code);
            Expect("ar exchange gain voucher still test-only " + code,
                Refused(delegate { ArapProcVoucherReq.TestGate(item, VoucherAsk("AR", "SYRAR0000000000001", "660399")); }) == code);
            Expect("ap exchange gain voucher still test-only " + code,
                Refused(delegate { ArapProcVoucherReq.TestGate(item, VoucherAsk("AP", "SYPAP0000000000001", "660399")); }) == code);
            Expect("bad debt voucher still test-only " + code,
                Refused(delegate { ArapProcVoucherReq.TestGate(item, VoucherAsk("AR", "HZAR0000000000001", null)); }) == code);
        }

        static ProcVoucherAsk VoucherAsk(string flag, string no, string plCode)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["flag"] = flag;
            body["cancel_nos"] = new List<object>(new object[] { no });
            if (plCode != null)
            {
                body["pl_code"] = plCode;
            }
            return ArapProcVoucherReq.Parse(body);
        }

        // 调用以 403 拒绝时返回错误码；没有拒绝或不是 403 时返回 null。
        static string Refused(Action act)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                return ex.Status == 403 ? ex.Code : null;
            }
            return null;
        }

        // 未配置策略：只分类、不拦，policy 为空。
        static void CheckGateOff()
        {
            WritePolicy.ResetForTest();
            AuditDraft draft = Draft(V("create"));
            WriteRequestInfo info = WriteClassGate.Pre(null, Body("sale_order", null), draft, "998", "op001");
            Expect("gate off pre", info != null && draft.Op == "create" && draft.Policy == "" && draft.TypeName == "sale_order");
            Expect("gate off read", WriteClassGate.Pre(null, Body("sale_order", null), Draft(V("load")), "998", "op001") == null);
            WorkItem item = Item("998");
            WriteClassGate.Post(item);
            Expect("gate off post", item.WriteInfo != null && item.WriteInfo.Op == "create" && item.Policy == "");
        }

        // 冻结 801：登录前、入队后都 503 write_frozen，审计记 op 和拒绝码；其他账套 allow。
        static void CheckGateFrozen()
        {
            WritePolicy.ResetForTest();
            try
            {
                WritePolicy.Apply("{\"version\":1,\"unlisted\":\"allow\",\"freeze\":{\"accounts\":[\"801\"]}}", DateTime.UtcNow);
                Expect("gate policy loaded", WritePolicy.State == "ok");
                AuditDraft frozen = Draft(V("create"));
                Expect("gate frozen pre", Refused(frozen, "801") == "write_frozen" && frozen.Policy == "write_frozen"
                    && frozen.Op == "create");
                AuditDraft open = Draft(V("create"));
                Expect("gate open pre", Refused(open, "802") == null && open.Policy == WriteClassGate.Allow);
                WorkItem item = Item("801");
                string code = null;
                try
                {
                    WriteClassGate.Post(item);
                }
                catch (BridgeException ex)
                {
                    code = ex.Code;
                }
                Expect("gate frozen post", code == "write_frozen" && item.Policy == "write_frozen");
            }
            finally
            {
                WritePolicy.ResetForTest();
            }
        }

        static string Refused(AuditDraft draft, string acc)
        {
            try
            {
                WriteClassGate.Pre(null, Body("sale_order", null), draft, acc, "op001");
                return null;
            }
            catch (BridgeException ex)
            {
                return ex.Code;
            }
        }

        static void Is(string name, string path, Dictionary<string, object> body, string type, string op)
        {
            WriteRequestInfo info = WriteClass.Of(path, body);
            Expect("class " + name, info != null && info.Type == type && info.Op == op && info.Path == path);
        }

        static string V(string op)
        {
            return "/u8co/v1/vouchers/" + op;
        }

        static Dictionary<string, object> Body(string type, string action)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            if (type != null)
            {
                body["type"] = type;
            }
            if (action != null)
            {
                body["action"] = action;
            }
            return body;
        }

        static Dictionary<string, object> Row(string key, object value)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            if (key != null)
            {
                row[key] = value;
            }
            return row;
        }

        static AuditDraft Draft(string path)
        {
            AuditDraft draft = new AuditDraft();
            draft.Path = path;
            return draft;
        }

        static WorkItem Item(string acc)
        {
            WorkItem item = new WorkItem();
            item.Config = new BridgeConfig();
            item.Path = V("create");
            item.Type = Kinds.Find("sale_order");
            item.Body = Body("sale_order", null);
            item.Acc = acc;
            item.Operator = "op001";
            return item;
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
