using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 写入分类（写入策略）：每条写路由映射到唯一的（type, op），WritePolicyEval 按账套的放行规则判断。
    // type：单据类路由（vouchers/*、workflow/*、旧版审核路由）取单据类型名；不带 type 的路由族用族名：
    // gl、archives、arap、notes、openings、periods、ia。
    // op：封闭词表 Ops。按 action 区分的路由逐值映射（"值=op"），未知或缺少的 action 记 other（之后的字段校验照旧 400）。
    // 取消类操作（取消核销、取消制单、取消处理、取消汇兑损益、取消记账）一律记 other，放行 writeoff / voucher / process / post 不连带放行取消。
    // WriteGate 的每条写路由都要在这里登记一行，--selftest（WriteClassSelfTest）逐条核对。
    internal static class WriteClass
    {
        // op 词表与策略文件的 ops 同一份（WritePolicySnapshot.OpNames）。
        public static readonly string[] Ops = WritePolicySnapshot.OpNames;

        const string V = "/u8co/v1/vouchers/";
        const string Wf = "/u8co/v1/workflow/";
        const string VerifyMap = "verify=verify,unverify=unverify,arap_verify=verify,arap_unverify=unverify";

        // 一行规则：Type 为空表示取请求体的 type；Op 含 "=" 时按请求体 action 映射，否则是固定的 op。
        sealed class Rule
        {
            public string Type;
            public string Op;
        }

        static readonly Dictionary<string, Rule> Rules = BuildRules();

        static Dictionary<string, Rule> BuildRules()
        {
            Dictionary<string, Rule> map = new Dictionary<string, Rule>(StringComparer.Ordinal);
            // 单据：审核 / 弃审（应收应付的发票复核 arap_verify / arap_unverify 也记 verify / unverify）、新增、修改、删除、
            // 关闭 / 打开、生单、锁定 / 解锁。
            Add(map, V + "verify", "", VerifyMap);
            Add(map, V + "create", "", "create");
            Add(map, V + "update", "", "update");
            Add(map, V + "delete", "", "delete");
            Add(map, V + "close", "", "close=close,open=open");
            Add(map, V + "generate", "", "generate");
            Add(map, Requests.LockPath, "", "lock=lock,unlock=unlock");
            // 审批流：七个操作都记 workflow，type 是单据类型。
            string[] wf = new string[] { "submit", "withdraw", "approve", "disagree", "return", "abandon", "resubmit" };
            for (int i = 0; i < wf.Length; i++)
            {
                Add(map, Wf + wf[i], "", "workflow");
            }
            // 旧版审核路由。
            Add(map, "/u8co/v1/sale-orders/verify", "sale_order", "verify=verify,unverify=unverify");
            Add(map, "/u8co/v1/dispatches/verify", "dispatch", "verify=verify,unverify=unverify");
            AddGl(map);
            // 档案：新增、修改、删除。
            Add(map, Requests.ArcRoot + "create", "archives", "create");
            Add(map, Requests.ArcRoot + "update", "archives", "update");
            Add(map, Requests.ArcRoot + "delete", "archives", "delete");
            AddArap(map);
            AddLedger(map);
            return map;
        }

        // 总账凭证：新增、修改、删除、审核、取消审核、记账照名；作废、取消作废、出纳签字、取消签字、取消记账记 other。
        static void AddGl(Dictionary<string, Rule> map)
        {
            string[] same = new string[] { "create", "update", "delete", "verify", "unverify", "post" };
            for (int i = 0; i < same.Length; i++)
            {
                Add(map, Requests.GlRoot + same[i], "gl", same[i]);
            }
            string[] other = new string[] { "void", "unvoid", "sign", "unsign", "unpost" };
            for (int i = 0; i < other.Length; i++)
            {
                Add(map, Requests.GlRoot + other[i], "gl", "other");
            }
            // 红字冲销（GlReverse）：填制一张红字凭证，功能权限同填制（GL0201），记 create。
            Add(map, Requests.GlRoot + "reverse", "gl", "create");
            // 期间损益结转、自定义转账（GlTransfer，测试账套）：按定义生成结转凭证，记 voucher（同制单），
            // 放行手工填制（create）不连带放行自动转账。
            Add(map, GlTransferReq.PnlPath, "gl", "voucher");
            Add(map, GlTransferReq.CustomPath, "gl", "voucher");
        }

        // 应收 / 应付（type arap）与票据（type notes）。
        static void AddArap(Dictionary<string, Rule> map)
        {
            // 核销、自动核销记 writeoff；取消核销 other。
            Add(map, Requests.WriteoffPath, "arap", "writeoff");
            Add(map, Requests.WriteoffAutoPath, "arap", "writeoff");
            Add(map, Requests.WriteoffCancelPath, "arap", "other");
            // 制单、处理制单记 voucher；取消制单 other。
            Add(map, Requests.ArapVoucherPath, "arap", "voucher");
            Add(map, ArapProcVoucherReq.Path, "arap", "voucher");
            Add(map, Requests.ArapVoucherDropPath, "arap", "other");
            // 并账、转账、红票对冲、汇兑损益、坏账处理记 process；取消处理、取消汇兑损益 other。
            Add(map, ArapMergeReq.Path, "arap", "process");
            Add(map, Requests.TransferPath, "arap", "process");
            Add(map, ArapRedReq.Path, "arap", "process");
            Add(map, ArapExGainReq.Path, "arap", "process");
            Add(map, ArapBadReq.Path, "arap", "process");
            Add(map, ArapProcCancelReq.Path, "arap", "other");
            Add(map, ArapExGainReq.CancelPath, "arap", "other");
            // 票据登记、删除、处理。
            Add(map, NotesRegReq.CreatePath, "notes", "create");
            Add(map, NotesRegReq.DeletePath, "notes", "delete");
            Add(map, NotesProcReq.Path, "notes", "process");
        }

        // 期初、月末结账、存货核算（都按 action 映射）。
        static void AddLedger(Dictionary<string, Rule> map)
        {
            Add(map, OpeningPostReq.Path, "openings", "post=post,unpost=other");
            Add(map, OpeningsArapReq.Path, "openings", "create=create,delete=delete,verify=verify,unverify=unverify");
            Add(map, PeriodCloseReq.Path, "periods", "close=close,reopen=open");
            Add(map, IaReq.PostPath, "ia", "post=post,unpost=other");
            Add(map, IaReq.PeriodEndPath, "ia", "run=close,cancel=open");
        }

        static void Add(Dictionary<string, Rule> map, string path, string type, string op)
        {
            Rule rule = new Rule();
            rule.Type = type;
            rule.Op = op;
            map.Add(path, rule);
        }

        // 登记过的写路由（自检用）。
        internal static ICollection<string> Paths()
        {
            return Rules.Keys;
        }

        internal static bool IsOp(string op)
        {
            return op != null && Array.IndexOf(Ops, op) >= 0;
        }

        // 规则里出现的全部 op（固定值和 action 映射的值），自检核对都在词表里。
        internal static List<string> RuleOps(string path)
        {
            List<string> ops = new List<string>();
            Rule rule;
            if (path == null || !Rules.TryGetValue(path, out rule))
            {
                return ops;
            }
            if (rule.Op.IndexOf('=') < 0)
            {
                ops.Add(rule.Op);
                return ops;
            }
            string[] pairs = rule.Op.Split(',');
            for (int i = 0; i < pairs.Length; i++)
            {
                ops.Add(pairs[i].Substring(pairs[i].IndexOf('=') + 1));
            }
            return ops;
        }

        // 按路径和请求体分类；不是写路由返回 null。Acc、Operator、DryRun 由调用方填。
        public static WriteRequestInfo Of(string path, Dictionary<string, object> body)
        {
            Rule rule;
            if (path == null || !Rules.TryGetValue(path, out rule))
            {
                return null;
            }
            WriteRequestInfo info = new WriteRequestInfo();
            info.Path = path;
            info.Type = rule.Type.Length > 0 ? rule.Type : (Requests.Field(body, "type") as string ?? "");
            // 固定资产卡片单列 type fa_card，放行 archives 不连带放行固定资产卡片。
            if (rule.Type == "archives" && (Requests.Field(body, "archive") as string) == ArcFa.Name)
            {
                info.Type = ArcFa.Name;
            }
            info.Op = OpOf(rule, Requests.Field(body, "action") as string);
            info.Lines = LinesOf(info.Op, body);
            bool bad;
            info.Amount = AmountOf(info.Type, info.Op, body, out bad);
            info.AmountBad = bad;
            return info;
        }

        // 入队后的任务：请求体在 item.Body（旧版审核路由没有，用 item.Action）。
        public static WriteRequestInfo Of(WorkItem item)
        {
            if (item == null)
            {
                return null;
            }
            Dictionary<string, object> body = item.Body;
            if (body == null)
            {
                body = new Dictionary<string, object>();
                body["action"] = item.Action;
                if (item.Type != null)
                {
                    body["type"] = item.Type.Name;
                }
            }
            WriteRequestInfo info = Of(item.Path, body);
            if (info == null)
            {
                return null;
            }
            info.Acc = item.Acc;
            info.Operator = item.Operator;
            info.DryRun = DryOf(item.Path, body, item.DryRun);
            return info;
        }

        // 自动核销的 dry_run 是它自己的试算参数（不经 DryRunReq），同样算预演。
        internal static bool DryOf(string path, Dictionary<string, object> body, bool dry)
        {
            if (dry)
            {
                return true;
            }
            object plan = path == Requests.WriteoffAutoPath ? Requests.Field(body, DryRunReq.Field) : null;
            return plan is bool && (bool)plan;
        }

        static string OpOf(Rule rule, string action)
        {
            if (rule.Op.IndexOf('=') < 0)
            {
                return rule.Op;
            }
            string[] pairs = rule.Op.Split(',');
            for (int i = 0; i < pairs.Length; i++)
            {
                int eq = pairs[i].IndexOf('=');
                if (action != null && string.Equals(pairs[i].Substring(0, eq), action, StringComparison.Ordinal))
                {
                    return pairs[i].Substring(eq + 1);
                }
            }
            return "other";
        }

        // 新增、修改、生单的表体行数（请求体 lines 数组）；其余为 0。
        static int LinesOf(string op, Dictionary<string, object> body)
        {
            if (op != "create" && op != "update" && op != "generate")
            {
                return 0;
            }
            ICollection lines = Requests.Field(body, "lines") as ICollection;
            return lines == null ? 0 : lines.Count;
        }

        // 按金额设上限的单据（WritePolicyEval.AmountTypes：收款单、付款单、客户退款、供应商退款）新增 / 修改的金额：
        // 表体 iAmt（本币）合计；某行只给原币 iAmt_f 时按表头 iExchRate（缺省 1）折算。
        // 收付款单表头没有金额列。一行金额都没有（如只改表头）返回 null，不查金额上限。
        // 取值与写入方 ArapReq 相同（转成文本、去空白、NumberStyles.Float，可带指数）。以下情形置 bad，配置了金额上限时
        // 按超过上限拒绝（不能靠写入方认、这里不认的写法绕过上限）：金额或汇率解析不了；同一字段有多个只差大小写的键
        // （写入方取哪一个与这里不一定相同）；合计或折算超出 decimal 范围；修改时某行只给原币（修改不收汇率，写入方按单据
        // 已存的汇率折算，这里无从得知）。金额上限按本次请求的明细行计，不含单据上未改动的行。
        static decimal? AmountOf(string type, string op, Dictionary<string, object> body, out bool bad)
        {
            bad = false;
            if ((op != "create" && op != "update") || Array.IndexOf(WritePolicyEval.AmountTypes, type) < 0)
            {
                return null;
            }
            ICollection lines = Requests.Field(body, "lines") as ICollection;
            if (lines == null)
            {
                return null;
            }
            Dictionary<string, object> head = Requests.Field(body, "head") as Dictionary<string, object>;
            decimal rate = Number(Cell(head, "iexchrate", ref bad), ref bad) ?? 1m;
            try
            {
                return Sum(lines, rate, op == "update", ref bad);
            }
            catch (OverflowException)
            {
                bad = true;
                return null;
            }
        }

        static decimal? Sum(ICollection lines, decimal rate, bool update, ref bool bad)
        {
            decimal sum = 0m;
            bool any = false;
            foreach (object raw in lines)
            {
                decimal? amount = LineAmount(raw as Dictionary<string, object>, rate, update, ref bad);
                if (amount.HasValue)
                {
                    sum += amount.Value;
                    any = true;
                }
            }
            return any ? (decimal?)sum : null;
        }

        // 本币、原币都解析（任一解析不了即 bad）；有本币取本币，否则原币 × 汇率（修改时只给原币置 bad）。
        static decimal? LineAmount(Dictionary<string, object> row, decimal rate, bool update, ref bool bad)
        {
            decimal? local = Number(Cell(row, "iamt", ref bad), ref bad);
            decimal? foreign = Number(Cell(row, "iamt_f", ref bad), ref bad);
            if (local.HasValue)
            {
                return local;
            }
            if (!foreign.HasValue)
            {
                return null;
            }
            if (update)
            {
                bad = true;
            }
            return foreign.Value * rate;
        }

        // 字段名不区分大小写（与 ArapReq.Fields 相同）；多个键只差大小写时置 bad，取第一个。
        static object Cell(Dictionary<string, object> row, string key, ref bool bad)
        {
            if (row == null)
            {
                return null;
            }
            object found = null;
            int hits = 0;
            foreach (KeyValuePair<string, object> pair in row)
            {
                if (string.Equals(pair.Key, key, StringComparison.OrdinalIgnoreCase))
                {
                    if (hits == 0)
                    {
                        found = pair.Value;
                    }
                    hits++;
                }
            }
            if (hits > 1)
            {
                bad = true;
            }
            return found;
        }

        // 与 ArapReq.Fields / Num 相同：空或空白算没给（返回 null）；其余按 NumberStyles.Float 解析，解析不了置 bad。
        static decimal? Number(object value, ref bool bad)
        {
            string text = Text(value);
            if (text == null)
            {
                bad = true;
                return null;
            }
            text = text.Trim();
            if (text.Length == 0)
            {
                return null;
            }
            decimal parsed;
            if (decimal.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out parsed))
            {
                return parsed;
            }
            bad = true;
            return null;
        }

        // 与 ArapReq.Cell 相同的转文本：null 为空串，布尔 1 / 0，数字按不变区域格式化；其他类型返回 null（写入方报 400）。
        static string Text(object value)
        {
            if (value == null || value is DBNull)
            {
                return "";
            }
            string text = value as string;
            if (text != null)
            {
                return text;
            }
            if (value is bool)
            {
                return (bool)value ? "1" : "0";
            }
            IFormattable fmt = value as IFormattable;
            return fmt == null ? null : fmt.ToString(null, CultureInfo.InvariantCulture);
        }
    }

    // 写入策略的两处检查（WritePolicy 未配置时什么都不做）：登录前（Requests.Build，账套名单之后、解密之前）和
    // 入队后（WorkRun.RequireAccount，登录 U8 之前）。两处都按决策顺序 1–6（WritePolicyEval）；测试账套名单另查，互不替代。
    // 只读账套（ReadOnlyGate）在两处都先于写入策略判断，策略放行也拦。
    // 审计：op 记分类结果，policy 记 allow 或拒绝的错误码；未配置策略时 policy 为空。
    internal static class WriteClassGate
    {
        public const string Allow = "allow";

        // 写路由请求体的 caller（审计用）：在 IdemReq.Take 取走之前读出，Take 校验通过后才记进草稿。没带或不是写路由为空。
        public static string CallerOf(Dictionary<string, object> body, string path)
        {
            if (!IdemReq.Supports(path))
            {
                return "";
            }
            return Requests.Field(body, IdemReq.CallerField) as string ?? "";
        }

        // 登录前：分类结果和判定记进审计草稿；拒绝时抛出（不解密、不入队）。返回分类结果（读路由为 null）。
        public static WriteRequestInfo Pre(
            BridgeConfig cfg, Dictionary<string, object> body, AuditDraft draft, string acc, string user)
        {
            string path = draft == null ? null : draft.Path;
            WriteRequestInfo info = WriteClass.Of(path, body);
            if (info == null)
            {
                // 未分类的写路由（WriteClassSelfTest 保证没有）同样受只读账套约束。
                ReadOnlyGate.Pre(cfg, draft, acc);
                return null;
            }
            info.Acc = acc;
            info.Operator = user;
            info.DryRun = WriteClass.DryOf(path, body, !string.IsNullOrEmpty(draft.DryRun));
            draft.Op = info.Op;
            // 拒绝时审计行也要有单据类型（ApplyType 还没跑）；路由族记号（gl、arap……）不进 type 列。
            if (Kinds.Find(info.Type) != null)
            {
                draft.TypeName = info.Type;
            }
            ReadOnlyGate.Pre(cfg, draft, acc);
            BridgeException refused = Decide(info, out draft.Policy);
            if (refused != null)
            {
                throw refused;
            }
            return info;
        }

        // 登录前的结果带进任务：入队后的审计行同样有 caller、op、policy。
        public static void Stamp(WorkItem item, AuditDraft draft, WriteRequestInfo info)
        {
            item.Caller = draft.Caller;
            item.WriteInfo = info;
            item.Policy = draft.Policy;
        }

        // 入队后：冻结、时段等可能在排队期间生效，按当时的策略快照再判一次。
        public static void Post(WorkItem item)
        {
            if (item == null)
            {
                return;
            }
            ReadOnlyGate.Post(item);
            WriteRequestInfo info = item.WriteInfo ?? WriteClass.Of(item);
            if (info == null)
            {
                return;
            }
            item.WriteInfo = info;
            BridgeException refused = Decide(info, out item.Policy);
            if (refused != null)
            {
                throw refused;
            }
        }

        static BridgeException Decide(WriteRequestInfo info, out string policy)
        {
            if (WritePolicy.State == "off")
            {
                policy = "";
                return null;
            }
            BridgeException refused = WritePolicyEval.Check(info, DateTime.Now);
            policy = refused == null ? Allow : refused.Code;
            return refused;
        }
    }
}
