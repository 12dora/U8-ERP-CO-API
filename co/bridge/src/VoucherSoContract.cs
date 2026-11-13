using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 销售订单合同号回写（vouchers/so_contract）：只改销售订单表头的合同号 cDefine1，给了 customer_contract_no 时
    // 另写客户合同号 cDefine2。U8 界面和 vouchers/update 都不让改已审核、已有下游的订单，这条路由是受控的例外：
    // 不碰表体、金额、数量、日期、部门、业务员、审核 / 关闭状态，不碰发货单、销售发票、各 History 表（已开出的下游单据上的合同号不跟着改）。
    // 一个事务里 UPDLOCK, ROWLOCK 读表头，查功能 / 数据权限、锁定人、长度、expect，按 Decide（纯判断）决定写什么，
    // 预演到此回滚，否则按 ufts 乐观锁 UPDATE，写修改人 / 修改日期，回读核对（VoucherSoContractSql）。
    // replace=true 时合同号已有别的值也直接覆盖（expect 照常核对当前值；客户合同号规则不变），用于合同改号。
    // 空值等价：NULL、空串、空白、「无」都是空。比较去两端空白、不分大小写。
    // 单据锁同修改销售订单（"sale_order:<id>"，DocLocks 按 type + id 派生）。
    // mode=pair（成对设置，VoucherSoContractPair）：cDefine1 合同号、cDefine2 内部合同号一起设成请求值（允许覆盖，expect 防并发），
    // cascade=true 时同一事务里把这一对写到下游发货单 / 退货单、销售出库单、销售发票表头（VoucherSoContractDown）。
    internal static class VoucherSoContract
    {
        public const string Op = "so_contract";
        public const string Path = "/u8co/v1/vouchers/" + Op;
        public const string Kind = "sale_order";
        public const string ContractKey = "contract_no";
        public const string CustomerKey = "customer_contract_no";
        public const string ReplaceKey = "replace";
        public const string ModeKey = "mode";
        public const string InternalKey = "internal_contract_no";
        public const string CascadeKey = "cascade";
        internal const string RuleKey = "write:voucher:sale_order:so_contract";
        internal const string Placeholder = "无";
        // 登录前字段表（RequestsP4.P4Specs）：路径加除公共字段外的顶层键。
        internal static readonly string[] Spec = new string[] { Path, "type", "id", ContractKey, CustomerKey, "expect", ReplaceKey,
            ModeKey, InternalKey, CascadeKey };
        // 请求值的上限（与 API 相同）；实际上限是 U8 自定义项设置与列宽，由 VoucherSoContractSql 在事务里核对。
        internal const int ValueMax = 120;

        // 登录前的类型闸门（Requests.KeyGuards）：只认销售订单。
        public static void GuardKind(VoucherKind kind)
        {
            if (kind == null || kind.Name != Kind)
            {
                throw new BridgeException(400, "bad_request", "合同号回写目前只支持 type=" + Kind, "type");
            }
        }

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Item == null || ctx.Item.Body == null)
            {
                throw new BridgeException(500, "internal", "合同号回写请求缺少请求体");
            }
            GuardKind(ctx.Item.Type);
            SoContractAsk ask = Parse(ctx.Item.Body);
            ask.Id = ctx.Item.Id;
            return VoucherSoContractSql.Run(ctx, ask);
        }

        // 校验 contract_no / customer_contract_no / expect / replace（id、type 已由 Requests 校验）。expect 必须带两个键，值是字符串或 null。
        // 带 mode 的走成对设置（VoucherSoContractPair.Parse）；不带 mode 时 internal_contract_no、cascade 一律 400。
        public static SoContractAsk Parse(Dictionary<string, object> body)
        {
            if (VoucherSoContractPair.Wanted(body))
            {
                return VoucherSoContractPair.Parse(body);
            }
            VoucherSoContractPair.RefuseOutside(body);
            SoContractAsk ask = new SoContractAsk();
            ask.ContractNo = Value(Required(body, ContractKey), ContractKey);
            object customer = Requests.Field(body, CustomerKey);
            ask.CustomerNo = customer == null ? null : Value(customer, CustomerKey);
            ask.Replace = body.ContainsKey(ReplaceKey) && Flag(body[ReplaceKey], ReplaceKey);
            Dictionary<string, object> expect = ExpectMap(body, CustomerKey);
            ask.Expect1 = ExpectText(expect, ContractKey);
            ask.Expect2 = ExpectText(expect, CustomerKey);
            return ask;
        }

        internal static object Required(Dictionary<string, object> body, string key)
        {
            object raw = Requests.Field(body, key);
            if (raw == null)
            {
                throw ArcReq.Bad("缺少 " + key, key);
            }
            return raw;
        }

        // expect：对象，只认 contract_no 和 key2 两个键（两个键是否都在由 ExpectText 查）。
        internal static Dictionary<string, object> ExpectMap(Dictionary<string, object> body, string key2)
        {
            Dictionary<string, object> expect = Requests.Field(body, "expect") as Dictionary<string, object>;
            if (expect == null)
            {
                throw ArcReq.Bad("expect 必须是对象，带 " + ContractKey + "、" + key2 + " 的当前值", "expect");
            }
            foreach (string key in expect.Keys)
            {
                if (key != ContractKey && key != key2)
                {
                    throw ArcReq.Bad("未知字段 " + key, FieldPath.Clean("expect." + key));
                }
            }
            return expect;
        }

        // replace / cascade：可省（省略即 false）；给了就必须是布尔值，显式 null 同样 400（与 API、客户端一致）。
        internal static bool Flag(object raw, string key)
        {
            if (!(raw is bool))
            {
                throw ArcReq.Bad(key + " 必须是 true 或 false", key);
            }
            return (bool)raw;
        }

        // 要写入的值：字符串，非空，不是「无」，前后没有空白，不超过 ValueMax，不含控制字符。
        internal static string Value(object raw, string label)
        {
            string text = raw as string;
            if (text == null)
            {
                throw ArcReq.Bad(label + " 必须是字符串", label);
            }
            if (text.Trim().Length == 0)
            {
                throw ArcReq.Bad(label + " 不能为空", label);
            }
            if (text.Trim().Length != text.Length)
            {
                throw ArcReq.Bad(label + " 前后不能有空白", label);
            }
            if (text == Placeholder)
            {
                throw ArcReq.Bad(label + " 不能是「" + Placeholder + "」", label);
            }
            CheckText(text, label);
            return text;
        }

        // expect 的值：必须给键；null 与空串等价；不要求写法合规（旧数据可能带首尾空白），只挡超长和控制字符。
        internal static string ExpectText(Dictionary<string, object> expect, string key)
        {
            string label = "expect." + key;
            if (!expect.ContainsKey(key))
            {
                throw ArcReq.Bad("expect 必须带 " + key + "（当前为空用 null 或 \"\"）", label);
            }
            object raw = expect[key];
            if (raw == null)
            {
                return "";
            }
            string text = raw as string;
            if (text == null)
            {
                throw ArcReq.Bad(label + " 必须是字符串或 null", label);
            }
            CheckText(text, label);
            return text;
        }

        static void CheckText(string text, string label)
        {
            if (text.Length > ValueMax)
            {
                throw ArcReq.Bad(label + " 长度不能超过 " + ValueMax.ToString(CultureInfo.InvariantCulture), label);
            }
            for (int i = 0; i < text.Length; i++)
            {
                if (text[i] < ' ' || text[i] == '\x7f')
                {
                    throw ArcReq.Bad(label + " 含有控制字符", label);
                }
            }
        }

        // 空：null、空串、空白、「无」（去两端空白后）。
        internal static bool Blank(string value)
        {
            string text = (value ?? "").Trim();
            return text.Length == 0 || text == Placeholder;
        }

        // 空值等价、去两端空白、不分大小写。
        internal static bool Same(string a, string b)
        {
            if (Blank(a) || Blank(b))
            {
                return Blank(a) && Blank(b);
            }
            return string.Equals(a.Trim(), b.Trim(), StringComparison.OrdinalIgnoreCase);
        }

        // 纯判断（自检覆盖）：按 expect 与当前值决定写哪些列。顺序：expect → 已是目标（changed=false）→ 客户合同号非空
        // → 合同号已有别的值时只允许原样挪进客户合同号（customer_contract_no 必须等于当前合同号）或 replace=true，否则不覆盖。
        internal static SoContractPlan Decide(SoContractAsk ask, SoContractSnap snap)
        {
            if (ask.Pair)
            {
                return VoucherSoContractPair.Decide(ask, snap);
            }
            CheckExpect(ask, snap);
            SoContractPlan plan = new SoContractPlan();
            plan.SetContract = !Same(snap.Contract, ask.ContractNo);
            plan.SetCustomer = ask.CustomerNo != null && !Same(snap.Customer, ask.CustomerNo);
            if (!plan.SetContract && !plan.SetCustomer)
            {
                return plan;
            }
            if (ask.CustomerNo != null)
            {
                RefuseCustomer(snap);
            }
            if (plan.SetContract)
            {
                RefuseOverwrite(ask, snap);
            }
            plan.Changed = true;
            return plan;
        }

        static void CheckExpect(SoContractAsk ask, SoContractSnap snap)
        {
            if (!Same(ask.Expect1, snap.Contract) || !Same(ask.Expect2, snap.Customer))
            {
                throw Mismatch("销售订单表头与 expect 不符：合同号 " + Show(snap.Contract) + "，客户合同号 "
                    + Show(snap.Customer), snap);
            }
        }

        // 给了 customer_contract_no 时客户合同号当前必须为空（不覆盖已有的客户合同号）。
        static void RefuseCustomer(SoContractSnap snap)
        {
            if (!Blank(snap.Customer))
            {
                throw Mismatch("客户合同号已有值 " + Show(snap.Customer) + "，不覆盖", snap);
            }
        }

        // 合同号当前已有别的值：replace=true（expect 已核对过当前值），或把它原样挪进客户合同号（customer_contract_no 等于当前合同号）时才写，否则不覆盖。
        static void RefuseOverwrite(SoContractAsk ask, SoContractSnap snap)
        {
            if (ask.Replace || Blank(snap.Contract))
            {
                return;
            }
            if (ask.CustomerNo == null || !Same(ask.CustomerNo, snap.Contract))
            {
                throw Mismatch("合同号已有其他值 " + Show(snap.Contract)
                    + "，不覆盖（改号用 replace=true；要把它挪进客户合同号，customer_contract_no 须等于它）", snap);
            }
        }

        // 锁定人非空且不是本操作员（按 U8 写 cLocker 的方式比较姓名）：409 state_mismatch，与 VoucherLockGate 同一错误码和原文，
        // 另在 detail 里给 reason=locked 和锁定人，调用方不必解析中文消息。
        internal static void CheckLocker(string locker, string user)
        {
            if (VoucherLockGate.Blocks(locker, user))
            {
                Dictionary<string, object> detail = new Dictionary<string, object>();
                detail["reason"] = "locked";
                detail["locker"] = locker.Trim();
                throw new BridgeException(409, "state_mismatch", "单据已被 " + locker.Trim() + " 锁定").WithDetail(detail);
            }
        }

        // 超过 U8 的长度（自定义项设置的长度与列宽取小）：400 bad_request（带字段名）。limit 为 0 表示不限。
        internal static void CheckLength(string value, int limit, string label)
        {
            if (value != null && limit > 0 && value.Length > limit)
            {
                throw BridgeException.BadField(label, label + " 超过 U8 自定义项的长度 "
                    + limit.ToString(CultureInfo.InvariantCulture));
            }
        }

        // expect 不符等：409 state_mismatch，detail.current 是两列的当前原值（NULL 为 null）。
        internal static BridgeException Mismatch(string message, SoContractSnap snap)
        {
            return ArcCustomerRow.Mismatch(message, Current(snap.Contract, snap.Customer));
        }

        internal static Dictionary<string, object> Current(string contract, string customer)
        {
            Dictionary<string, object> current = new Dictionary<string, object>();
            current[ContractKey] = contract;
            current[CustomerKey] = customer;
            return current;
        }

        internal static string Show(string value)
        {
            return value == null || value.Trim().Length == 0 ? "空" : value.Trim();
        }

        // 功能权限（销售订单变更，PermRegistrySoContract）+ 表头数据权限（客户、部门、业务员、销售类型）。
        internal static void Permit(WorkContext ctx, Dictionary<string, object> head)
        {
            PermContext p = PermCheck.Of(ctx);
            PermRule rule = PermRegistry.ForKey(RuleKey);
            PermCheck.RequireRule(p, rule);
            if (!PermCheck.RowAllowed(p, rule, head))
            {
                throw new BridgeException(403, "no_permission", PermCheck.DeniedRow);
            }
        }

        // 响应：changed 是表头有没有（会不会）变；current 是写后（预演和 changed=false 时是当前）的两列；ufts 是行版本。
        internal static Dictionary<string, object> Body(SoContractAsk ask, SoContractSnap snap, bool changed)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = Kind;
            body["id"] = ask.Id;
            body["code"] = snap.Code;
            body["changed"] = changed;
            body["current"] = ask.Pair ? VoucherSoContractPair.Current(snap.Contract, snap.Customer)
                : Current(snap.Contract, snap.Customer);
            body["ufts"] = snap.Ts;
            return body;
        }

        // 预演（validate）：检查都已在事务里做完；changed 是「真写会不会改」，current 是当前值。
        internal static Dictionary<string, object> DryBody(SoContractAsk ask, SoContractSnap snap, bool changed)
        {
            Dictionary<string, object> body = Body(ask, snap, changed);
            body["dry_run"] = true;
            body["mode"] = DryRunModes.Validate;
            return body;
        }
    }

    internal sealed class SoContractAsk
    {
        public int Id;
        public string ContractNo;
        // cDefine2 的目标值；null 表示不动 cDefine2。
        public string CustomerNo;
        // expect 的两个当前值（null 已转成空串）。
        public string Expect1;
        public string Expect2;
        // replace=true：合同号已有别的值也覆盖。
        public bool Replace;
        // mode=pair：CustomerNo 存内部合同号（cDefine2 的目标值，必填），两列都按请求值设。
        public bool Pair;
        // cascade=true（只在 pair 模式）：连带更新下游单据表头。
        public bool Cascade;

        // cDefine2 在请求里的字段名（错误里的 field 用）。
        public string Key2
        {
            get { return Pair ? VoucherSoContract.InternalKey : VoucherSoContract.CustomerKey; }
        }
    }

    // 表头快照：Contract / Customer 是 cDefine1 / cDefine2 的原值（NULL 为 null）。
    internal sealed class SoContractSnap
    {
        public string Code;
        public string Contract;
        public string Customer;
        public string Locker;
        public string Ts;
    }

    internal sealed class SoContractPlan
    {
        public bool Changed;
        public bool SetContract;
        public bool SetCustomer;
    }
}
