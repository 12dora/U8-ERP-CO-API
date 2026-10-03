using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的写预演部分：模式表、dry_run 请求解析、预演单元格写法、回读 SQL、meta。只跑纯函数，不连库、不建 COM。
    internal static class DryRunSelfTest
    {
        public static void Run()
        {
            CheckVoucherModes();
            CheckKindDrift();
            CheckOtherModes();
            CheckGlob();
            CheckValues();
            CheckRequest();
            CheckIdle();
            CheckSql();
            CheckFit();
            CheckMeta();
        }

        static void CheckVoucherModes()
        {
            Mode("so create", "rollback", "vouchers/create", "sale_order", "create", "");
            Mode("prefixed route", "rollback", "/u8co/v1/vouchers/update", "sale_order", "update", "");
            Mode("mo create", "validate", "vouchers/create", "production_order", "create", "");
            Mode("mo verify", "validate", "vouchers/verify", "production_order", "verify", "");
            Mode("mo close", "rollback", "vouchers/close", "production_order", "close", "");
            Mode("bom verify", "validate", "vouchers/verify", "bom", "verify", "");
            Mode("qm check generate", "validate", "vouchers/generate", "qm_incoming_check", "generate", "qm_incoming_inspect");
            Mode("qm inspect generate", "rollback", "vouchers/generate", "qm_incoming_inspect", "generate", "arrival");
            Mode("qm delete", "validate", "vouchers/delete", "qm_product_inspect", "delete", "");
            Mode("sale_out generate", "rollback", "vouchers/generate", "sale_out", "generate", "dispatch");
            Mode("arap verify", "rollback", "vouchers/verify", "purchase_invoice", "arap_verify", "");
            Mode("so lock", "rollback", "vouchers/lock", "sale_order", "lock", "");
            Mode("po lock refuse", "refuse", "vouchers/lock", "purchase_order", "lock", "");
            Mode("new kind refuse", "refuse", "vouchers/create", "future_kind", "create", "");
            Mode("reject delete", "validate", "vouchers/delete", "qm_product_reject", "delete", "");
            Mode("reject generate", "validate", "vouchers/generate", "qm_incoming_reject", "generate", "qm_incoming_check");
            Mode("reject update refuse", "refuse", "vouchers/update", "qm_product_reject", "update", "");
            Mode("bom create", "validate", "vouchers/create", "bom", "create", "");
        }

        // Kinds.cs 里每种单据支持的每个写操作都要在 DryRunModes 有明确的行；故意不支持预演的写进 Refused（"类型 操作"）。
        // 反过来，模式表里的类型名都要在 Kinds.cs 里存在。新增类型或操作忘了登记这里就失败。
        static readonly string[] Refused = new string[0];

        static void CheckKindDrift()
        {
            foreach (VoucherKind k in Kinds.All())
            {
                Covered(k, "create", k.Creatable, "");
                Covered(k, "update", k.Updatable, "");
                Covered(k, "delete", k.Deletable, "");
                Covered(k, "verify", k.Verifiable, "");
                Covered(k, "close", k.Closable, "");
                Covered(k, "lock", VoucherLock.Lockable(k), "");
                string[] sources = k.Sources ?? new string[0];
                for (int i = 0; i < sources.Length; i++)
                {
                    Covered(k, "generate", true, sources[i]);
                }
            }
            List<string> named = DryRunModes.VoucherKindNames();
            for (int i = 0; i < named.Count; i++)
            {
                Expect("dry kind exists " + named[i], Kinds.Find(named[i]) != null);
            }
        }

        static void Covered(VoucherKind k, string op, bool on, string source)
        {
            if (!on)
            {
                return;
            }
            string mode = DryRunModes.Lookup("vouchers/" + op, k.Name, op, source);
            bool refused = Array.IndexOf(Refused, k.Name + " " + op) >= 0;
            Expect("dry kind row " + k.Name + " " + op, refused ? mode == "refuse" : mode != "refuse");
        }

        static void CheckOtherModes()
        {
            Mode("gl create", "validate", "gl/vouchers/create", "", "gl_create", "");
            Mode("gl post", "validate", "gl/vouchers/post", "", "gl_post", "");
            Mode("gl void", "rollback", "gl/vouchers/void", "", "gl_void", "");
            Mode("arc project", "rollback", "archives/create", "project", "archive_create", "");
            Mode("arc customer", "validate", "archives/create", "customer", "archive_create", "");
            Mode("arc currency create", "validate", "archives/create", "currency", "archive_create", "");
            Mode("arc currency update", "rollback", "archives/update", "currency", "archive_update", "");
            Mode("arc contact", "validate", "archives/delete", "customer_contact", "archive_delete", "");
            Mode("wf approve", "validate", "workflow/approve", "sale_order", "approve", "");
            Mode("writeoff", "rollback", "arap/writeoff", "", "writeoff", "");
            Mode("arap voucher", "validate", "arap/voucher", "ar_bill", "arap_voucher", "");
            Mode("arap voucher drop", "rollback", "arap/voucher/delete", "", "arap_voucher_delete", "");
            Mode("auto plan", "plan", "arap/writeoff/auto", "", "writeoff_auto", "");
            Mode("legacy refuse", "refuse", "sale-orders/verify", "", "verify", "");
            Mode("read refuse", "refuse", "vouchers/load", "sale_order", "", "");
            WorkItem arc = new WorkItem();
            arc.Path = Requests.ArcRoot + "create";
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["archive"] = "vendor_bank";
            Expect("mode of archive", DryRunModes.ModeOf(arc, body) == "rollback");
        }

        static void CheckGlob()
        {
            Expect("glob any", DryRunModes.Match("*", ""));
            Expect("glob mid", DryRunModes.Match("qm_*_check", "qm_product_check"));
            Expect("glob mid miss", !DryRunModes.Match("qm_*_check", "qm_product_inspect"));
            Expect("glob short", !DryRunModes.Match("qm_*_check", "qm_check"));
            Expect("glob exact", DryRunModes.Match("bom", "bom") && !DryRunModes.Match("bom", "boms"));
        }

        static void CheckValues()
        {
            Json("null", null, false, null);
            Json("dbnull", DBNull.Value, false, null);
            Json("binary", new byte[] { 1, 2 }, false, null);
            Json("string trim", "A01  ", true, "A01");
            Json("date", new DateTime(2024, 6, 1), true, "2024-06-01");
            Json("datetime", new DateTime(2024, 6, 1, 8, 30, 5), true, "2024-06-01T08:30:05");
            Json("decimal", 5.5000m, true, 5.5d);
            Json("bool", true, true, 1);
            Json("short", (short)3, true, 3);
            Json("long", 12345678901L, true, 12345678901L);
            Expect("keep ufts", !DryRunValue.Keep("UFTS", 200));
            Expect("keep binary", !DryRunValue.Keep("cDefine", 128) && !DryRunValue.Keep("x", 205));
            Expect("keep password", !DryRunValue.Keep("cPassword", 202));
            Expect("keep normal", DryRunValue.Keep("cInvCode", 202));
            List<object> rb = DryRunRun.Warnings("rollback", "create");
            Expect("warn create", rb.Count == 2 && (string)rb[0] == "number_may_skip" && (string)rb[1] == "locks_held");
            List<object> up = DryRunRun.Warnings("rollback", "update");
            Expect("warn update", up.Count == 1 && (string)up[0] == "locks_held");
            List<object> va = DryRunRun.Warnings("validate", "create");
            Expect("warn validate", va.Count == 1 && (string)va[0] == "validate_only");
            Expect("route short", DryRunRun.RouteOf("/u8co/v1/gl/vouchers/create") == "gl/vouchers/create");
        }

        static void CheckRequest()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["dry_run"] = true;
            Expect("take write", DryRunReq.Take(body, "/u8co/v1/vouchers/create") && !body.ContainsKey("dry_run"));
            body["dry_run"] = true;
            Expect("read keeps", !DryRunReq.Take(body, "/u8co/v1/vouchers/load") && body.ContainsKey("dry_run"));
            Expect("auto keeps", !DryRunReq.Take(body, Requests.WriteoffAutoPath) && body.ContainsKey("dry_run"));
            body["dry_run"] = "yes";
            Throws400("take not bool", delegate { DryRunReq.Take(body, "/u8co/v1/vouchers/delete"); });
            body["dry_run"] = false;
            Throws400("legacy", delegate { DryRunReq.RefuseLegacy(body, new AuditDraft()); });
            IdemAsk ask = new IdemAsk();
            Throws400("idem", delegate { DryRunReq.RefuseIdem(true, ask); });
            DryRunReq.RefuseIdem(false, ask);
            DryRunReq.RefuseIdem(true, null);
            Expect("accepts", DryRunReq.Accepts(Requests.GlRoot + "void") && !DryRunReq.Accepts("/u8co/v1/dispatches/verify"));
            WorkItem item = new WorkItem();
            item.Path = "/u8co/v1/vouchers/create";
            item.Type = Kinds.Find("sale_order");
            item.Action = "create";
            DryRunReq.Apply(item, new Dictionary<string, object>(), true);
            Expect("apply mode", item.DryRun && item.DryRunMode == "rollback");
            item.Path = "/u8co/v1/vouchers/load";
            Throws400("apply refuse", delegate { DryRunReq.Apply(item, new Dictionary<string, object>(), true); });
        }

        // 不是预演任务时，处理函数的登记和 Stop 都不做事。
        static void CheckIdle()
        {
            DryRun.End();
            Expect("idle", !DryRun.Active && !DryRun.Finished);
            DryRun.Created(Kinds.Find("sale_order"), 1);
            DryRun.Touched(Kinds.Find("sale_order"), 1);
            DryRun.Set("x", 1);
            DryRun.Stop(null, "nothing");
            DryRun.BeforeBegin();
            Expect("still idle", !DryRun.Active);
        }

        static void CheckSql()
        {
            VoucherKind so = Kinds.Find("sale_order");
            Expect("head sql", DryRunPreview.HeadSql(so) == "SELECT * FROM [SO_SOMain] WHERE [ID] = ?");
            Expect("lines sql", DryRunPreview.LinesSql(so, false)
                == "SELECT TOP 200 * FROM [SO_SODetails] WHERE [ID] = ? ORDER BY [iSOsID]");
            string ar = DryRunPreview.LinesSql(Kinds.Find("ar_bill"), true);
            Expect("lines sql link", ar
                == "SELECT COUNT(*) FROM [Ap_Vouchs] WHERE [cLink] IN (SELECT h.[cLink] FROM [Ap_Vouch] h WHERE h.[Auto_ID] = ?)");
        }

        // 超过 4 MiB 的 docs 去掉 lines、保留 lines_total；小的不动。
        static void CheckFit()
        {
            Dictionary<string, object> small = new Dictionary<string, object>();
            small["lines"] = new List<object>();
            small["lines_total"] = 0;
            List<object> docs = new List<object>();
            docs.Add(small);
            Expect("fit small", !DryRunPreview.Fit(docs, null) && small.ContainsKey("lines"));
            Dictionary<string, object> big = new Dictionary<string, object>();
            List<object> lines = new List<object>();
            lines.Add(new string('x', 5 * 1024 * 1024));
            big["lines"] = lines;
            big["lines_total"] = 1;
            docs.Add(big);
            List<object> few = new List<object>();
            few.Add(small);
            Dictionary<string, object> detail = new Dictionary<string, object>();
            detail["plan"] = new string('y', 5 * 1024 * 1024);
            Expect("fit detail", DryRunPreview.Fit(few, detail) && !small.ContainsKey("lines"));
            Expect("fit big", DryRunPreview.Fit(docs, null) && !big.ContainsKey("lines") && big.ContainsKey("lines_total"));
        }

        static void CheckMeta()
        {
            Dictionary<string, object> mo = MetaDryRun.Of(Kinds.Find("production_order"));
            Expect("meta mo", (string)mo["create"] == "validate" && (string)mo["close"] == "rollback");
            Dictionary<string, object> so = MetaDryRun.Of(Kinds.Find("sale_order"));
            Expect("meta so", (string)so["lock"] == "rollback" && so.ContainsKey("close") && !so.ContainsKey("generate"));
            Dictionary<string, object> routes = MetaDryRun.Routes();
            Dictionary<string, object> arc = routes["archives/create"] as Dictionary<string, object>;
            Expect("meta arc", arc != null && (string)arc["project"] == "rollback" && (string)arc["*"] == "validate");
            Expect("meta gl", (string)routes["gl/vouchers/create"] == "validate" && (string)routes["arap/writeoff/auto"] == "plan");
            Expect("meta refs", (string)MetaRefs.FieldRefs()["cowhcode"] == "warehouse"
                && (string)MetaRefs.FieldRefs()["inv_code"] == "inventory" && !MetaRefs.FieldRefs().ContainsKey("cdwcode")
                && (string)MetaRefs.GlFieldRefs()["supplier"] == "vendor");
        }

        static void Mode(string name, string want, string route, string type, string action, string source)
        {
            Expect("dry mode " + name, DryRunModes.Lookup(route, type, action, source) == want);
        }

        static void Json(string name, object raw, bool keep, object want)
        {
            object got;
            bool kept = DryRunValue.TryJson(raw, out got);
            Expect("dry value " + name, kept == keep && (!keep || object.Equals(got, want)));
        }

        static void Throws400(string name, Action act)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                Expect("dry 400 " + name, ex.Status == 400 && ex.Code == "bad_request");
                return;
            }
            throw new InvalidOperationException("dry 400 " + name);
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
