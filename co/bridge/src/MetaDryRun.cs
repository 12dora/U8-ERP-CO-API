using System.Collections.Generic;

namespace U8Co
{
    // meta 的预演部分：kinds[].dry_run（只列该类型支持的操作）和顶层 dry_run_routes（单据路由以外的写路由）。
    // 值都从 DryRunModes 算，改模式表 meta 随之变（revision 跟着变）。
    internal static class MetaDryRun
    {
        static readonly string[] OtherRoutes = new string[]
        {
            "gl/vouchers/create", "gl/vouchers/update", "gl/vouchers/void", "gl/vouchers/unvoid", "gl/vouchers/verify",
            "gl/vouchers/unverify", "gl/vouchers/sign", "gl/vouchers/unsign", "gl/vouchers/delete", "gl/vouchers/post",
            // 红字冲销（GlReverse）。
            "gl/vouchers/reverse",
            // 取消记账（GlUnpost，测试账套）。
            "gl/vouchers/unpost",
            // 期间损益结转、自定义转账（GlTransfer，测试账套）。
            "gl/transfer/pnl", "gl/transfer/custom",
            "archives/create", "archives/update", "archives/delete",
            "arap/writeoff", "arap/writeoff/cancel", "arap/writeoff/auto", "arap/voucher", "arap/voucher/delete", "arap/merge",
            "openings/post", "openings/arap", "periods/close", "ia/post", "ia/period_end",
            // 取消应收冲应付 / 应付冲应收 / 并账（ArapProcCancel）。
            "arap/process/cancel",
            // 应收冲应付 / 应付冲应收（ArapTransfer）。
            "arap/transfer",
            // 红票对冲（ArapRed）。
            "arap/red_offset",
            // 票据处理（NotesProc）。
            "notes/process",
            // 票据登记、删除（NotesReg、NotesRegDel）。
            "notes/create", "notes/delete",
            // 处理制单（ArapProcVoucher）。
            "arap/process/voucher",
            // 汇兑损益、取消汇兑损益（ArapExGain、ArapExGainCancel）。
            "arap/exchange_gain", "arap/exchange_gain/cancel",
            // 坏账处理（ArapBad：坏账发生、坏账收回、计提坏账准备）。
            "arap/bad_debt"
        };

        internal static Dictionary<string, object> Of(VoucherKind k)
        {
            Dictionary<string, object> d = new Dictionary<string, object>();
            AddIf(d, k.Creatable, "create", k, "vouchers/create", "create");
            AddIf(d, k.Updatable, "update", k, "vouchers/update", "update");
            AddIf(d, k.Deletable, "delete", k, "vouchers/delete", "delete");
            AddIf(d, k.Verifiable, "verify", k, "vouchers/verify", "verify");
            AddIf(d, k.Closable, "close", k, "vouchers/close", "close");
            AddIf(d, VoucherLock.Lockable(k), "lock", k, "vouchers/lock", "lock");
            if (k.Sources != null && k.Sources.Length > 0)
            {
                Dictionary<string, object> gen = new Dictionary<string, object>();
                for (int i = 0; i < k.Sources.Length; i++)
                {
                    gen[k.Sources[i]] = DryRunModes.Mode("vouchers/generate", k, "generate", k.Sources[i]);
                }
                d["generate"] = gen;
            }
            return d;
        }

        static void AddIf(Dictionary<string, object> d, bool on, string op, VoucherKind k, string route, string action)
        {
            if (on)
            {
                d[op] = DryRunModes.Mode(route, k, action, "");
            }
        }

        // 路由规则只有 * 时给字符串；分档案的给 {"<archive>": mode, "*": mode}。审批流各路由同一模式。
        internal static Dictionary<string, object> Routes()
        {
            Dictionary<string, object> d = new Dictionary<string, object>();
            for (int i = 0; i < OtherRoutes.Length; i++)
            {
                d[OtherRoutes[i]] = RouteMode(OtherRoutes[i]);
            }
            for (int i = 0; i < DryRunModes.WorkflowRoutes.Length; i++)
            {
                d[DryRunModes.WorkflowRoutes[i]] = RouteMode(DryRunModes.WorkflowRoutes[i]);
            }
            return d;
        }

        static object RouteMode(string route)
        {
            List<string> named = DryRunModes.NamedTypes(route);
            string any = DryRunModes.Lookup(route, "*", "", "");
            if (named.Count == 0)
            {
                return any;
            }
            Dictionary<string, object> map = new Dictionary<string, object>();
            for (int i = 0; i < named.Count; i++)
            {
                map[named[i]] = DryRunModes.Lookup(route, named[i], "", "");
            }
            map["*"] = any;
            return map;
        }
    }
}
