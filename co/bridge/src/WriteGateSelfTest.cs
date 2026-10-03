using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的全局写闸门部分（WriteGate、DocLocks.KeysOf）：锁键、路由表的读写登记、迟到写入不开始。不连库、不建 COM。
    internal static class WriteGateSelfTest
    {
        // 按 WorkItem 断言时开关取这个值：CheckWrites 设成本轮的开关，读路由在开关打开时断言。
        static bool _on = true;

        public static void Run()
        {
            CheckWrites(true);
            _on = true;
            CheckReads();
            CheckWrites(false);
            Expect("write gate default", new BridgeConfig().SerializeWrites && WriteGate.Enabled(new WorkItem()));
            CheckRouteTable();
            CheckGlDispatched();
            CheckLateWrite();
            CheckModes();
            CheckAccountKeys();
        }

        // serializeWrites 的三种取值：缺省与 true 全局、false 关闭、"account" 按账套；其他值启动失败。
        static void CheckModes()
        {
            BridgeConfig cfg = new BridgeConfig();
            WriteGate.LoadMode(cfg, Mode(null));
            Expect("mode default", cfg.SerializeWrites && !cfg.SerializePerAccount);
            WriteGate.LoadMode(cfg, Mode(false));
            Expect("mode false", !cfg.SerializeWrites && !cfg.SerializePerAccount);
            WriteGate.LoadMode(cfg, Mode(WriteGate.AccountMode));
            Expect("mode account", cfg.SerializeWrites && cfg.SerializePerAccount);
            WriteGate.LoadMode(cfg, Mode(true));
            Expect("mode true", cfg.SerializeWrites && !cfg.SerializePerAccount);
            Expect("mode bad string", BadMode("global"));
            Expect("mode bad number", BadMode(1));
        }

        // 按账套：持 "u8:write:<账套>"、不持全局键，不同账套键不同；读路由不持；serializeWrites=false 时不持。
        static void CheckAccountKeys()
        {
            string[] a = AccountKeys(Voucher("update", "sale_order", 7), "801", true);
            Expect("account key", Has(a, WriteGate.AccountPrefix + "801") && !Has(a, WriteGate.Key) && Has(a, "sale_order:7"));
            string[] b = AccountKeys(Voucher("create", "sale_order", 0), "802", true);
            Expect("account key other", Has(b, WriteGate.AccountPrefix + "802") && !Has(b, WriteGate.AccountPrefix + "801"));
            string[] read = AccountKeys(Voucher("load", "sale_order", 7), "801", true);
            Expect("account key read", !Has(read, WriteGate.AccountPrefix + "801") && !Has(read, WriteGate.Key));
            string[] off = AccountKeys(Voucher("update", "sale_order", 7), "801", false);
            Expect("account key off", !Has(off, WriteGate.AccountPrefix + "801") && !Has(off, WriteGate.Key));
        }

        static string[] AccountKeys(WorkItem item, string acc, bool on)
        {
            item.Acc = acc;
            item.Config.SerializeWrites = on;
            item.Config.SerializePerAccount = true;
            return DocLocks.KeysOf(item);
        }

        static Dictionary<string, object> Mode(object value)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            if (value != null)
            {
                map["serializeWrites"] = value;
            }
            return map;
        }

        static bool BadMode(object value)
        {
            try
            {
                WriteGate.LoadMode(new BridgeConfig(), Mode(value));
            }
            catch (InvalidOperationException)
            {
                return true;
            }
            return false;
        }

        // Dispatch 路由表里的读路由（显式列出）。表里其余路由都必须登记在 WriteGate，新写路由忘了登记这里就失败。
        static bool ReadRoute(string path)
        {
            string[] reads = new string[]
            {
                "/u8co/v1/vouchers/load", "/u8co/v1/workflow/state", "/u8co/v1/workflow/history", "/u8co/v1/workflow/tasks",
                Requests.GlRoot + "load", Requests.GlRoot + "list", Requests.ArcRoot + "get", Requests.ArcRoot + "list",
                Requests.ListPath, Requests.StockPath, GlAttach.Path, VoucherAttach.Path,
                // 核销记录查询：只读报表，不持写闸门。
                Requests.ReportRoot + ReportsArapWriteoffReq.Name,
                // 名称解析、幂等结果查询：只读，不持写闸门。
                ArcResolve.Path, IdemGet.Path,
                // 单据搜索、单据批量读取、档案批量读取：只读，不持写闸门。
                VoucherSearch.Path, LoadMany.Path, ArcGetMany.Path,
                // 字段说明：只读，不持写闸门。
                MetaFieldsReq.Path,
                // 单张票据读取：只读，不持写闸门。
                NotesReadReq.Path,
                // 应收 / 应付处理记录：只读，不持写闸门。
                ArapProcListReq.Path,
                // 总账凭证摘要（事件服务用）：只读，不持写闸门。
                GlDigest.Path,
                // 权限快照、权限评估：只读，不持写闸门。
                PermSnapshot.Path, PermEvaluate.Path
            };
            return Array.IndexOf(reads, path) >= 0 || path.StartsWith(Requests.ReportRoot, StringComparison.Ordinal);
        }

        // 每条路由恰好是读路由或已登记的写路由之一；另外单独核对关闭、锁定。
        static void CheckRouteTable()
        {
            List<string> paths = DispatchRoutes.Paths();
            for (int i = 0; i < paths.Count; i++)
            {
                WorkItem item = Item(paths[i], null, 0, null);
                Expect("route classified " + paths[i], WriteGate.IsWrite(item) != ReadRoute(paths[i]));
            }
            Expect("route close", WriteGate.IsWrite(Voucher("close", "sale_order", 7)));
            Expect("route lock", WriteGate.IsWrite(Item(Requests.LockPath, "sale_order", 7, null)));
        }

        // 反向：写闸门、登录前字段表里登记的每条总账路由都必须在 Dispatch 路由表里，否则请求一律 404「未知路径」。
        static void CheckGlDispatched()
        {
            List<string> routes = DispatchRoutes.Paths();
            List<string> gl = DispatchRoutes.WritePaths();
            gl.AddRange(DispatchRoutes.SpecPaths());
            foreach (string path in gl)
            {
                if (path.StartsWith(Requests.GlRoot, StringComparison.Ordinal))
                {
                    Expect("gl route dispatched " + path, routes.Contains(path));
                }
            }
            Expect("gl unpost dispatched", routes.Contains(GlUnpostReq.Path) && routes.Contains(GlReverseReq.Path));
        }

        // 写路由剩不到 MinWriteStartMs 就不开始；读路由不受影响。
        static void CheckLateWrite()
        {
            long now = DateTime.UtcNow.Ticks;
            long late = now + (WorkItem.MinWriteStartMs - 1000) * TimeSpan.TicksPerMillisecond;
            long early = now + (WorkItem.MinWriteStartMs + 1000) * TimeSpan.TicksPerMillisecond;
            WorkItem write = Voucher("update", "sale_order", 7);
            write.DeadlineUtcTicks = late;
            Expect("late write", WorkItem.TooLateToWrite(write, now));
            write.DeadlineUtcTicks = early;
            Expect("early write", !WorkItem.TooLateToWrite(write, now));
            WorkItem read = Voucher("load", "sale_order", 7);
            read.DeadlineUtcTicks = late;
            Expect("late read", !WorkItem.TooLateToWrite(read, now));
        }

        // 开关开时写入都持 "u8:write"；关时都不持，单据锁照旧。
        static void CheckWrites(bool on)
        {
            string tag = on ? " on" : " off";
            _on = on;
            Gated("create" + tag, Voucher("create", "sale_order", 0), on);
            Gated("update" + tag, Voucher("update", "sale_order", 7), on);
            Gated("delete" + tag, Voucher("delete", "purchase_order", 7), on);
            Gated("verify" + tag, Voucher("verify", "purchase_order", 7), on);
            Gated("generate" + tag, Voucher("generate", "dispatch", 7), on);
            Gated("workflow approve" + tag, Item("/u8co/v1/workflow/approve", "sale_order", 7, null), on);
            Gated("gl post" + tag, Item(Requests.GlRoot + "post", null, 0, GlBody()), on);
            Gated("gl verify" + tag, Item(Requests.GlRoot + "verify", null, 0, GlBody()), on);
            string[] arc = Keys(Item(Requests.ArcRoot + "create", null, 0, ArcBody()), on);
            Gated("archive create" + tag, arc, on);
            Expect("archive create keeps arc:write" + tag, Has(arc, "arc:write") && Has(arc, "arc:department:D01"));
            string[] upd = Keys(Voucher("update", "sale_order", 7), on);
            Expect("update keeps doc key" + tag, Has(upd, "sale_order:7"));
            WorkItem legacy = Item("/u8co/v1/sale-orders/verify", null, 7, null);
            legacy.Kind = WorkItem.SaleKind;
            Gated("legacy verify" + tag, legacy, on);
        }

        static void CheckReads()
        {
            Gated("load", Voucher("load", "sale_order", 7), false);
            Gated("list", Item(Requests.ListPath, "sale_order", 0, null), false);
            Gated("report", Item(Requests.ReportRoot + "gl_balance", null, 0, null), false);
            Gated("report arap_writeoffs", Item(Requests.ReportRoot + ReportsArapWriteoffReq.Name, null, 0, null), false);
            Gated("workflow tasks", Item("/u8co/v1/workflow/tasks", null, 0, null), false);
            Gated("workflow state", Item("/u8co/v1/workflow/state", "sale_order", 7, null), false);
            Gated("gl load", Item(Requests.GlRoot + "load", null, 0, GlBody()), false);
            Gated("archive get", Item(Requests.ArcRoot + "get", null, 0, ArcBody()), false);
            Gated("stock current", Item(Requests.StockPath, null, 0, null), false);
            Gated("archive resolve", Item(ArcResolve.Path, null, 0, null), false);
            Gated("idempotency get", Item(IdemGet.Path, null, 0, null), false);
            Gated("voucher search", Item(VoucherSearch.Path, "sale_order", 0, null), false);
            Gated("archive get_many", Item(ArcGetMany.Path, null, 0, null), false);
            Gated("load_many", Item(LoadMany.Path, "sale_order", 0, null), false);
            Gated("meta fields", Item(MetaFieldsReq.Path, null, 0, null), false);
            Expect("null item", !WriteGate.IsWrite((WorkItem)null) && DocLocks.KeysOf(null).Length == 0);
        }

        static void Gated(string name, WorkItem item, bool want)
        {
            Gated(name, Keys(item, _on), want);
        }

        static void Gated(string name, string[] keys, bool want)
        {
            Expect("write gate " + name, Has(keys, WriteGate.Key) == want);
        }

        static string[] Keys(WorkItem item, bool on)
        {
            item.Config.SerializeWrites = on;
            return DocLocks.KeysOf(item);
        }

        static WorkItem Voucher(string op, string type, int id)
        {
            return Item("/u8co/v1/vouchers/" + op, type, id, null);
        }

        static WorkItem Item(string path, string type, int id, Dictionary<string, object> body)
        {
            WorkItem item = new WorkItem();
            item.Config = new BridgeConfig();
            item.Path = path;
            item.Type = type == null ? null : Kinds.Find(type);
            item.HasId = id > 0;
            item.Id = id;
            item.Body = body;
            item.Date = "2026-01-31";
            return item;
        }

        static Dictionary<string, object> GlBody()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["period"] = 1;
            body["sign"] = "记";
            body["no"] = 3;
            return body;
        }

        static Dictionary<string, object> ArcBody()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["archive"] = "department";
            body["code"] = "d01";
            return body;
        }

        static bool Has(string[] keys, string key)
        {
            return keys != null && Array.IndexOf(keys, key) >= 0;
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
