using System;
using System.Collections.Generic;

namespace U8Co
{
    // 预演模式表：(路由, 单据类型或档案, action, 生单来源) → rollback | validate | plan；表里没有的组合一律 refuse（登录前 400）。
    // 从上往下第一条命中为准。* 匹配任意值（含空），qm_* 这类只带一个 * 的写法按前缀和后缀匹配。
    // rollback：真跑 U8 组件 / SQL，在事务里回读后回滚；validate：自己提交的组件（EAI、凭证导入、U8API、审批流服务等）之前停下。
    // 依据探查（各写路径的事务类别）；联调后按测试账套实测再调。meta 的 kinds[].dry_run、dry_run_routes 也从这里算。
    internal static class DryRunModes
    {
        public const string Rollback = "rollback";
        public const string Validate = "validate";
        public const string Plan = "plan";
        public const string Refuse = "refuse";
        const string Any = "*";
        const string RoutePrefix = "/u8co/v1/";

        internal static readonly string[] WorkflowRoutes = new string[]
        {
            "workflow/submit", "workflow/withdraw", "workflow/approve", "workflow/disagree", "workflow/return",
            "workflow/abandon", "workflow/resubmit"
        };

        sealed class Rule
        {
            public string Route;
            public string Type;
            public string Action;
            public string Source;
            public string Mode;
        }

        static List<Rule> Build()
        {
            List<Rule> list = new List<Rule>();
            AddVouchers(list);
            AddGl(list);
            AddArchives(list);
            AddMany(list, WorkflowRoutes, Any, Any, Validate);
            Add(list, "arap/writeoff", Any, Any, Any, Rollback);
            Add(list, "arap/writeoff/cancel", Any, Any, Any, Rollback);
            // 取消应收冲应付 / 应付冲应收 / 并账：请求连接上按 U8 取消操作执行的 SQL 写（发票回写经 clsWrite2Bill，同一连接），提交钩子回滚。
            Add(list, "arap/process/cancel", Any, Any, Any, Rollback);
            // 并账：请求连接上按 U8 界面执行的 SQL 写（含取号过程），提交钩子回滚。
            Add(list, "arap/merge", Any, Any, Any, Rollback);
            // 应收冲应付 / 应付冲应收：请求连接上的 SQL 和销售发票回写组件（不自行提交），提交钩子回滚。
            Add(list, "arap/transfer", Any, Any, Any, Rollback);
            // 红票对冲：U8 的 AP_JZ_Red 加入请求连接的事务（实测能回滚），核对之后提交钩子回滚。
            Add(list, "arap/red_offset", Any, Any, Any, Rollback);
            // 票据处理：请求连接上按 U8 界面执行的 SQL 写（含取号过程），提交钩子回滚。
            Add(list, "notes/process", Any, Any, Any, Rollback);
            // 票据登记、删除：票据行是请求连接上的 SQL，收款单是 UFAPBO（同一事务），提交钩子回滚。
            Add(list, "notes/create", Any, Any, Any, Rollback);
            Add(list, "notes/delete", Any, Any, Any, Rollback);
            // 自动核销的 dry_run 是它自己的试算（ArapAutoWriteoff.Dry），不经 DryRun。
            Add(list, "arap/writeoff/auto", Any, Any, Any, Plan);
            // 制单：Prepare 算完、提交之前停（计划凭证在 detail）；凭证导入会自行提交。
            Add(list, "arap/voucher", Any, Any, Any, Validate);
            Add(list, "arap/voucher/delete", Any, Any, Any, Rollback);
            // 处理制单：同制单，Prepare 算完、提交之前停（计划凭证在 detail）。
            Add(list, "arap/process/voucher", Any, Any, Any, Validate);
            // 汇兑损益、取消汇兑损益：请求连接上的脚本和发票回写组件（不自行提交），提交钩子回滚。
            Add(list, "arap/exchange_gain", Any, Any, Any, Rollback);
            Add(list, "arap/exchange_gain/cancel", Any, Any, Any, Rollback);
            // 坏账处理（发生、收回、计提）：请求连接上的脚本和销售发票回写组件（不自行提交），提交钩子回滚。
            Add(list, "arap/bad_debt", Any, Any, Any, Rollback);
            // 期初记账 / 取消记账：请求连接上的 SQL，提交钩子回滚。
            Add(list, "openings/post", Any, Any, Any, Rollback);
            // 应收 / 应付期初单据：与 ar_bill / ap_bill 相同，UFAPBO 在请求连接的事务里，提交钩子回滚。
            Add(list, "openings/arap", Any, Any, Any, Rollback);
            // 月末结账 / 取消结账：同样是请求连接上的 SQL。
            Add(list, "periods/close", Any, Any, Any, Rollback);
            // 存货核算记账、期末处理：请求连接上的脚本，没有自行提交，提交钩子回滚。
            Add(list, "ia/post", Any, Any, Any, Rollback);
            Add(list, "ia/period_end", Any, Any, Any, Rollback);
            return list;
        }

        // 单据类型逐个列出（Kinds.cs 现有的类型和它们支持的操作）；以后新增的类型不在表里，预演一律 refuse，核实后再加。
        // 每行：类型名，然后是 rollback 的操作，"|" 之后是 validate 的操作。生单按类型列，来源不限（Kinds.Sources 已在登录前核对）。
        // 生产订单新增 / 修改 / 删除 / 审核、物料清单走 U8API（自行提交）；检验单生单、质量单据删除（含报检单删除前的弃审）
        // 调 VoucherOperate 自行提交。生产订单关闭 / 打开是请求连接上的存储过程，可回滚。
        static readonly string[] VoucherKinds = new string[]
        {
            "sale_order create update delete verify close lock",
            "dispatch create update delete verify generate",
            "sale_invoice create update delete verify generate",
            "sale_return update delete verify generate",
            // K12 退货申请单：销售 CO（VT 34）的保存、删除、审核转给 SaVoucherService 自行提交（ReturnsApplyTran），停在调用之前。
            "sale_return_apply | create update delete verify",
            "purchase_order create update delete verify close",
            // 到货单关闭 / 打开（PuArrClose）在请求连接的事务里。
            "arrival create update delete verify generate close",
            "purchase_invoice update delete verify generate",
            "purchase_return update delete verify generate",
            "purchase_requisition create update delete verify close",
            // 采购结算单：自动结算（bRdBVAutoSettle）、删除都在请求连接的事务里（测试账套实测可回滚）；
            // 手工结算（PuSettleMan）是请求连接上的 SQL，同样在事务里。
            "purchase_settle create delete generate",
            "purchase_in create update delete verify generate",
            "other_in create update delete verify",
            "other_out create update delete verify",
            // 期初结存新增走 EAI 导入（自行提交），停在 ProcessEx 之前；删除、审核在请求连接的事务里。
            "stock_opening delete verify | create",
            "product_in update delete verify generate",
            "material_out create update delete verify generate",
            "sale_out create update delete verify generate",
            "transfer create update delete verify generate",
            "shape_change create update delete verify",
            "transfer_request create update delete verify",
            "stock_check create delete",
            // 货位调整单：USERPCO 在请求连接的事务里，同形态转换单。
            "position_adjust create delete verify",
            "production_order close | create update delete verify",
            "bom | create update delete verify",
            "qm_incoming_inspect generate | delete",
            // 产品报检单单独弃审（unconfirm）由 U8 自己提交（QmInsUnverify）。
            "qm_product_inspect generate | delete verify",
            "qm_incoming_check | generate delete update",
            "qm_product_check | generate delete update",
            // 不良品处理单：保存、审核、弃审、删除都由 U8 自己提交（QmRejGen / QmRejOps）。
            "qm_incoming_reject | generate verify delete",
            "qm_product_reject | generate verify delete",
            // 其他报检单新增、审核、弃审、删除，其他检验单生单、审核、弃审、删除：VO 接口，都由 U8 自己提交（QmOthIns / QmOthChk / QmOthOps）。
            // 修改：VoucherOperate(update) 与 VO 的 UpdateVoucher 都由 U8 自己提交（QmEdit / QmOthEdit）。
            "qm_other_inspect | create verify delete update",
            "qm_other_check | generate verify delete update",
            "ar_receipt create update delete verify",
            "ap_payment create update delete verify",
            "ar_bill create update delete verify",
            "ap_bill create update delete verify",
            // 供应商退款、客户退款：同收付款单，UFAPBO 在请求连接的事务里。
            "ap_refund create update delete verify",
            "ar_refund create update delete verify"
        };

        // 必须写在 VoucherKinds（以及 Build 用到的其他静态数组）之后：静态字段按文本顺序初始化。
        static readonly List<Rule> Rules = Build();

        // 模式表里列出的单据类型名（--selftest 核对它们都在 Kinds.cs 里）。
        internal static List<string> VoucherKindNames()
        {
            List<string> names = new List<string>();
            for (int i = 0; i < VoucherKinds.Length; i++)
            {
                names.Add(VoucherKinds[i].Split(' ')[0]);
            }
            return names;
        }

        static void AddVouchers(List<Rule> list)
        {
            for (int i = 0; i < VoucherKinds.Length; i++)
            {
                string[] parts = VoucherKinds[i].Split(' ');
                string mode = Rollback;
                for (int j = 1; j < parts.Length; j++)
                {
                    if (parts[j] == "|")
                    {
                        mode = Validate;
                        continue;
                    }
                    Add(list, "vouchers/" + parts[j], parts[0], Any, Any, mode);
                }
            }
        }

        static void AddGl(List<Rule> list)
        {
            string[] sql = new string[] { "void", "unvoid", "verify", "unverify", "sign", "unsign", "delete" };
            for (int i = 0; i < sql.Length; i++)
            {
                Add(list, "gl/vouchers/" + sql[i], Any, Any, Any, Rollback);
            }
            // 新增 / 修改走 U8PzInsert.Transact，记账走 TransactionScope + VouchPostAll：都停在组件之前。
            Add(list, "gl/vouchers/create", Any, Any, Any, Validate);
            Add(list, "gl/vouchers/update", Any, Any, Any, Validate);
            Add(list, "gl/vouchers/post", Any, Any, Any, Validate);
            // 取消记账：请求连接上的一个事务（GlUnpost），走到提交点由钩子回滚。
            Add(list, "gl/vouchers/unpost", Any, Any, Any, Rollback);
            // 红字冲销：Prepare 的事务（取外部业务号）走到提交点由钩子回滚并结束，凭证导入不调用（同 arap/voucher）。
            Add(list, "gl/vouchers/reverse", Any, Any, Any, Validate);
            // 期间损益结转、自定义转账：算完、校验完停在凭证导入之前（GlTransfer.Preview）。
            Add(list, "gl/transfer/pnl", Any, Any, Any, Validate);
            Add(list, "gl/transfer/custom", Any, Any, Any, Validate);
        }

        // 档案的「类型」列是 archive。项目、客户 / 供应商银行是桥自己的 SQL；币种、凭证类别的修改删除是 SQL，新增走 EAI 式组件；
        // 其余（EAI Transact、客户联系人 CRM EAI）一律 validate。
        static void AddArchives(List<Rule> list)
        {
            string[] ops = new string[] { "archives/create", "archives/update", "archives/delete" };
            AddMany(list, ops, "project", Any, Rollback);
            AddMany(list, ops, "customer_bank", Any, Rollback);
            AddMany(list, ops, "vendor_bank", Any, Rollback);
            string[] sqlOps = new string[] { "archives/update", "archives/delete" };
            AddMany(list, sqlOps, "currency", Any, Rollback);
            AddMany(list, sqlOps, "voucher_sign", Any, Rollback);
            // 汇率、供应商联系人：新增走 EAI 分发器（validate），修改、删除是受控 SQL。
            AddMany(list, sqlOps, "exchange_rate", Any, Rollback);
            AddMany(list, sqlOps, "vendor_contact", Any, Rollback);
            AddMany(list, ops, Any, Any, Validate);
        }

        static void AddMany(List<Rule> list, string[] routes, string type, string action, string mode)
        {
            for (int i = 0; i < routes.Length; i++)
            {
                Add(list, routes[i], type, action, Any, mode);
            }
        }

        static void Add(List<Rule> list, string route, string type, string action, string source, string mode)
        {
            Rule rule = new Rule();
            rule.Route = route;
            rule.Type = type;
            rule.Action = action;
            rule.Source = source;
            rule.Mode = mode;
            list.Add(rule);
        }

        // 单据类路由：type 是单据类型，source 是生单来源类型名（可空）。路由带不带 /u8co/v1/ 前缀都可以。
        public static string Mode(string route, VoucherKind type, string action, string source)
        {
            return Lookup(route, type == null ? "" : type.Name, action, source);
        }

        // 按类型名（档案路由传 archive）查。
        public static string Lookup(string route, string type, string action, string source)
        {
            string r = Short(route);
            for (int i = 0; i < Rules.Count; i++)
            {
                Rule rule = Rules[i];
                if (rule.Route == r && Match(rule.Type, type) && Match(rule.Action, action) && Match(rule.Source, source))
                {
                    return rule.Mode;
                }
            }
            return Refuse;
        }

        // 一张已解析的任务：档案路由按 body.archive，其余按 Type / Action / Source。
        public static string ModeOf(WorkItem item, Dictionary<string, object> body)
        {
            string route = Short(item.Path);
            string type = item.Type == null ? "" : item.Type.Name;
            if (route.StartsWith("archives/", StringComparison.Ordinal))
            {
                type = Requests.Field(body, "archive") as string ?? "";
            }
            string source = item.Source == null ? "" : item.Source.Name;
            return Lookup(route, type, item.Action ?? "", source);
        }

        // meta 用：该路由的规则里出现过的具名类型（不含 *），按表内顺序。
        internal static List<string> NamedTypes(string route)
        {
            List<string> names = new List<string>();
            string r = Short(route);
            for (int i = 0; i < Rules.Count; i++)
            {
                string t = Rules[i].Type;
                if (Rules[i].Route == r && t != Any && t.IndexOf('*') < 0 && !names.Contains(t))
                {
                    names.Add(t);
                }
            }
            return names;
        }

        internal static bool Match(string pattern, string value)
        {
            string v = value ?? "";
            if (pattern == Any)
            {
                return true;
            }
            int star = pattern.IndexOf('*');
            if (star < 0)
            {
                return pattern == v;
            }
            string head = pattern.Substring(0, star);
            string tail = pattern.Substring(star + 1);
            return v.Length >= head.Length + tail.Length
                && v.StartsWith(head, StringComparison.Ordinal) && v.EndsWith(tail, StringComparison.Ordinal);
        }

        static string Short(string route)
        {
            if (route == null)
            {
                return "";
            }
            return route.StartsWith(RoutePrefix, StringComparison.Ordinal) ? route.Substring(RoutePrefix.Length) : route;
        }
    }
}
