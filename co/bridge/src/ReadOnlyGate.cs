using System;
using System.Text;

namespace U8Co
{
    // 只读账套（config.json 的 readOnlyAccounts）：名单里的账套只开放读取。WriteGate 登记的每条写路由一律
    // 403 account_read_only，含预演（dry_run 的预演、校验、回滚三种模式都会登录 U8）与幂等重放；写入策略文件、
    // 测试账套名单都不能放行。登录前在 WriteClassGate.Pre（账套名单之后、解密之前，先于写入策略）查，
    // 入队后在 WriteClassGate.Post（登录 U8 之前）再查一次。审计 policy 记 account_read_only。
    internal static class ReadOnlyGate
    {
        public const string Code = "account_read_only";
        public const string Text = "该账套只开放读取";
        public const string Hint = "这个账套在桥的 config.json 的 readOnlyAccounts 里，只能调用读取接口";

        // 健康检查与 meta 用的名单（AppHost 启动时设一次）；未设时为空。
        static volatile string[] _accounts = new string[0];

        public static void Configure(BridgeConfig cfg)
        {
            string[] list = cfg == null ? null : cfg.ReadOnlyAccounts;
            _accounts = list == null ? new string[0] : (string[])list.Clone();
        }

        // 当前名单的副本。
        public static string[] Accounts()
        {
            return (string[])_accounts.Clone();
        }

        // 登录前：写路由且账套在只读名单里时记审计 policy 并抛出。
        public static void Pre(BridgeConfig cfg, AuditDraft draft, string acc)
        {
            if (draft == null || !Refuses(cfg, draft.Path, acc))
            {
                return;
            }
            draft.Policy = Code;
            throw Refused();
        }

        // 入队后：同样的判断，审计 policy 记在任务上。
        public static void Post(WorkItem item)
        {
            if (item == null || !Refuses(item.Config, item.Path, item.Acc))
            {
                return;
            }
            item.Policy = Code;
            throw Refused();
        }

        public static bool Refuses(BridgeConfig cfg, string path, string acc)
        {
            return WriteGate.IsWrite(path) && BridgeConfig.IsReadOnlyAccount(cfg, acc);
        }

        static BridgeException Refused()
        {
            return new BridgeException(403, Code, Text).WithHint(Hint);
        }

        // 追加到健康检查 JSON 末尾（以逗号开头）：,"read_only_accounts":["801",...]。账套号已校验为 3 位数字。
        public static string HealthFragment()
        {
            string[] list = _accounts;
            StringBuilder sb = new StringBuilder(",\"read_only_accounts\":[");
            for (int i = 0; i < list.Length; i++)
            {
                if (i > 0)
                {
                    sb.Append(',');
                }
                sb.Append('"').Append(list[i]).Append('"');
            }
            return sb.Append(']').ToString();
        }

        // --check-config 的说明行。
        public static string Describe(BridgeConfig cfg)
        {
            string[] list = cfg == null || cfg.ReadOnlyAccounts == null ? new string[0] : cfg.ReadOnlyAccounts;
            string label = "readOnlyAccounts（只开放读取的账套）: ";
            return label + (list.Length == 0 ? "无" : string.Join(",", list) + "（这些账套的写入一律 403 account_read_only，含预演）");
        }
    }
}
