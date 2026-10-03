using System;
using System.Collections.Generic;

namespace U8Co
{
    // 全局写闸门（配置 serializeWrites，缺省开启）。实测：--jobs 4 回归时几个 STA 工人偶尔一起卡在 U8 组件里
    // （握着 SQL 锁，SQL Server 不报死锁），看门狗重启服务。开启后经 U8 组件的写入一律再持全局键 "u8:write"，同一时刻只跑一笔写；
    // 只跑 SQL 的读路由（读线程池）和写线程池上的读路由（COM 读单、workflow/tasks 等）不持这个键，照常并行。
    // 和 DocLocks 一样只用于排程：键被占用的写入留在队列里，StaPool.Pick 跳过它去取后面键空闲的任务，不会有线程拿着键等待。
    // 新增写路由要在这里登记，否则它不受闸门约束；同时在 WriteClass 登记它的（type, op），写入策略按它判断。
    // serializeWrites 可写 "account"：键改为按账套的 "u8:write:<账套>"，同一账套的写入串行、不同账套并行
    // （的卡死出现在同一账套的并行写入上；跨账套并行要先实测再推荐）。
    internal static class WriteGate
    {
        public const string Key = "u8:write";
        public const string AccountPrefix = "u8:write:";
        public const string AccountMode = "account";
        const string VoucherRoot = "/u8co/v1/vouchers/";
        const string WorkflowRoot = "/u8co/v1/workflow/";

        static readonly HashSet<string> Writes = BuildWrites();

        static HashSet<string> BuildWrites()
        {
            HashSet<string> set = new HashSet<string>(StringComparer.Ordinal);
            // 单据：审核 / 弃审（action）、新增、修改、删除、关闭 / 打开、生单、锁定 / 解锁（Requests.LockPath）。
            AddOps(set, VoucherRoot, new string[] { "verify", "create", "update", "delete", "close", "generate", "lock" });
            // 审批流：提交、撤销、同意、不同意、退回、弃审、重新提交（state / history / tasks 是读）。
            AddOps(set, WorkflowRoot, new string[] { "submit", "withdraw", "approve", "disagree", "return", "abandon", "resubmit" });
            // 总账凭证：load / list / attachments 是读；post（记账）先登记；unpost（取消记账，测试账套）。
            AddOps(set, Requests.GlRoot, new string[]
            {
                "create", "update", "void", "unvoid", "verify", "unverify", "sign", "unsign", "delete", "post", "unpost",
                // 红字冲销（GlReverse）。
                "reverse"
            });
            // 档案：get / list 是读。
            AddOps(set, Requests.ArcRoot, new string[] { "create", "update", "delete" });
            // 旧版审核路由。
            set.Add("/u8co/v1/sale-orders/verify");
            set.Add("/u8co/v1/dispatches/verify");
            // 应收 / 应付核销（ArapWriteoff）。
            set.Add(Requests.WriteoffPath);
            // 取消核销（ArapUnwriteoff）。
            set.Add(Requests.WriteoffCancelPath);
            // 取消应收冲应付 / 应付冲应收 / 并账（ArapProcCancel）：按 U8「取消操作」执行的写 SQL。
            set.Add(ArapProcCancelReq.Path);
            // 自动核销（ArapAutoWriteoff；dry_run 同一路由，同样排程）。
            set.Add(Requests.WriteoffAutoPath);
            // 并账（ArapMerge）：按 U8 并账界面执行的写 SQL。
            set.Add(ArapMergeReq.Path);
            // 应收冲应付 / 应付冲应收（ArapTransfer）：按 U8 转账界面执行的写 SQL。
            set.Add(Requests.TransferPath);
            // 红票对冲（ArapRed）：U8 的 AP_JZ_Red 组件，在请求连接的事务里。
            set.Add(ArapRedReq.Path);
            // 票据处理（NotesProc）：按 U8 票据管理界面执行的写 SQL。
            set.Add(NotesProcReq.Path);
            // 票据登记、删除（NotesReg、NotesRegDel）：票据行 SQL 加 UFAPBO 收款单，同一事务。
            set.Add(NotesRegReq.CreatePath);
            set.Add(NotesRegReq.DeletePath);
            // 应收 / 应付制单、取消制单（ArapVoucher、ArapVoucherDrop）。
            set.Add(Requests.ArapVoucherPath);
            set.Add(Requests.ArapVoucherDropPath);
            // 处理制单（ArapProcVoucher）。
            set.Add(ArapProcVoucherReq.Path);
            // 汇兑损益、取消汇兑损益（ArapExGain、ArapExGainCancel）：按 U8 汇兑损益界面执行的写 SQL。
            set.Add(ArapExGainReq.Path);
            set.Add(ArapExGainReq.CancelPath);
            // 坏账处理（ArapBad）：按 U8 坏账处理界面执行的写 SQL。
            set.Add(ArapBadReq.Path);
            // 期初记账 / 取消记账（OpeningPost）：按 U8 界面执行的写 SQL。
            set.Add(OpeningPostReq.Path);
            // 应收 / 应付期初单据（OpeningsArap）：经 UFAPBO。
            set.Add(OpeningsArapReq.Path);
            // 月末结账 / 取消结账（PeriodClose）：按 U8 界面执行的改标志 SQL。
            set.Add(PeriodCloseReq.Path);
            // 存货核算记账、期末处理（IaRun）：请求连接上整批执行内嵌脚本。
            set.Add(IaReq.PostPath);
            set.Add(IaReq.PeriodEndPath);
            // 期间损益结转、自定义转账（GlTransfer）：凭证导入 U8PzInsert.Transact 加补标记 SQL。
            set.Add(GlTransferReq.PnlPath);
            set.Add(GlTransferReq.CustomPath);
            return set;
        }

        static void AddOps(HashSet<string> set, string root, string[] ops)
        {
            for (int i = 0; i < ops.Length; i++)
            {
                set.Add(root + ops[i]);
            }
        }

        // 只看路由路径：同一路由不分单据类型，都经 U8 组件或按 U8 界面执行的写 SQL。幂等重放也按写入排程。
        public static bool IsWrite(WorkItem item)
        {
            return item != null && IsWrite(item.Path);
        }

        // 按路径判断（幂等键 IdemReq.Supports、写预演 DryRunReq.Accepts 都以这张表为准）。
        public static bool IsWrite(string path)
        {
            return path != null && Writes.Contains(path);
        }

        // 取不到配置时按缺省（开启）处理。
        public static bool Enabled(WorkItem item)
        {
            return item == null || item.Config == null || item.Config.SerializeWrites;
        }

        // 本任务持的闸门键：按账套时 "u8:write:<账套>"，否则全局 "u8:write"。
        public static string KeyOf(WorkItem item)
        {
            if (item != null && item.Config != null && item.Config.SerializePerAccount)
            {
                return AccountPrefix + (item.Acc ?? "");
            }
            return Key;
        }

        // config.json 的 serializeWrites：true（缺省，全局一把键）、false（只按单据锁排程）、"account"（按账套串行）。
        public static void LoadMode(BridgeConfig cfg, Dictionary<string, object> map)
        {
            cfg.SerializeWrites = true;
            cfg.SerializePerAccount = false;
            object raw;
            if (map == null || !map.TryGetValue("serializeWrites", out raw))
            {
                return;
            }
            if (raw is bool)
            {
                cfg.SerializeWrites = (bool)raw;
                return;
            }
            if (raw as string == AccountMode)
            {
                cfg.SerializePerAccount = true;
                return;
            }
            throw new InvalidOperationException("serializeWrites 必须是 true、false 或 \"account\"");
        }

        // --check-config 的说明（LicenseRules）。
        public static string ModeLabel(BridgeConfig cfg)
        {
            if (!cfg.SerializeWrites)
            {
                return "false（只按单据锁排程，写入可以并行）";
            }
            if (cfg.SerializePerAccount)
            {
                return "\"account\"（按账套串行：同一账套一次一笔写，不同账套并行）";
            }
            return "true（全局串行：一次一笔写）";
        }

        // 写入在单据锁之外追加闸门键（KeyOf）；不改传入的数组（DocLocks.None 是共享的）。
        public static string[] Apply(WorkItem item, string[] keys)
        {
            if (!Enabled(item) || !IsWrite(item))
            {
                return keys;
            }
            string[] held = keys ?? new string[0];
            string[] all = new string[held.Length + 1];
            Array.Copy(held, all, held.Length);
            all[held.Length] = KeyOf(item);
            return all;
        }
    }
}
