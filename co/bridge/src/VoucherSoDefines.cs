using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 销售订单表头自定义项写入（vouchers/so_defines）：把调用方指定的表头文本自定义项（cdefine1 … cdefine16）设成请求值。
    // 订单已审核、已有下游单据时也能写（vouchers/update 不允许）；不碰表体、金额、数量、日期、部门、业务员、审核 / 关闭状态和
    // 各 History 表。本路由只写不清空：请求值不能为空，也不能等于 empty_values 的任一项。
    // 一个事务里 UPDLOCK, ROWLOCK 读表头，查功能 / 数据权限、锁定人、列类型与长度、expect，按 Decide（纯判断）决定写哪些列，
    // 预演到此回滚，否则按 ufts 乐观锁只 UPDATE 有变化的列和修改人 / 修改日期，回读核对（VoucherSoDefinesSql）。
    // 空值等价：NULL、空串、空白和 empty_values 的各项（去两端空白后）都是空，彼此相等；其余去两端空白后逐字比较（区分大小写）。
    // cascade=true 时在同一事务里把这些列写到下游发货单 / 退货单、销售出库单、销售发票表头（VoucherSoDefinesDown）。
    // 单据锁同修改销售订单（"sale_order:<id>"，DocLocks 按 type + id 派生）。
    internal static class VoucherSoDefines
    {
        public const string Op = "so_defines";
        public const string Path = "/u8co/v1/vouchers/" + Op;
        public const string Kind = "sale_order";
        public const string FieldsKey = "fields";
        public const string ExpectKey = "expect";
        public const string EmptyKey = "empty_values";
        public const string CascadeKey = "cascade";
        internal const string RuleKey = "write:voucher:sale_order:so_defines";
        // 请求里的列键是 cdefine1 … cdefine16，不分大小写，规范成小写。
        internal const string KeyPrefix = "cdefine";
        internal const int MaxColumn = 16;
        // 登录前字段表（RequestsSpecs.ExtraSpecs）：路径加除公共字段外的顶层键。
        internal static readonly string[] Spec = new string[] { Path, "type", "id", FieldsKey, ExpectKey, EmptyKey, CascadeKey };
        // 请求值的上限（与 API 相同）；实际上限是 U8 自定义项设置与列宽，由 VoucherSoDefinesSql 在事务里核对。
        internal const int ValueMax = 120;
        // empty_values 最多几项、每项（去两端空白后）最长几个字符。
        internal const int EmptyMax = 8;
        internal const int EmptyLength = 20;

        // 登录前的类型闸门（Requests.KeyGuards）：只认销售订单。
        public static void GuardKind(VoucherKind kind)
        {
            if (kind == null || kind.Name != Kind)
            {
                throw new BridgeException(400, "bad_request", "表头自定义项写入目前只支持 type=" + Kind, "type");
            }
        }

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Item == null || ctx.Item.Body == null)
            {
                throw new BridgeException(500, "internal", "表头自定义项写入请求缺少请求体");
            }
            GuardKind(ctx.Item.Type);
            SoDefinesAsk ask = Parse(ctx.Item.Body);
            ask.Id = ctx.Item.Id;
            return VoucherSoDefinesSql.Run(ctx, ask);
        }

        // 校验 fields / expect / empty_values / cascade（id、type 已由 Requests 校验）。
        public static SoDefinesAsk Parse(Dictionary<string, object> body)
        {
            SoDefinesAsk ask = new SoDefinesAsk();
            ask.Empty = EmptyValues(Requests.Field(body, EmptyKey));
            Dictionary<string, object> fields = Normalise(Map(body, FieldsKey), FieldsKey);
            if (fields.Count == 0 || fields.Count > MaxColumn)
            {
                throw ArcReq.Bad(FieldsKey + " 须有 1 到 " + MaxColumn.ToString(CultureInfo.InvariantCulture) + " 项", FieldsKey);
            }
            ask.Keys.AddRange(fields.Keys);
            ask.Keys.Sort(delegate(string a, string b) { return Number(a).CompareTo(Number(b)); });
            foreach (string key in ask.Keys)
            {
                ask.Values[key] = Value(fields[key], FieldsKey + "." + key, ask.Empty);
            }
            ParseExpect(ask, Normalise(Map(body, ExpectKey), ExpectKey));
            ask.Cascade = body.ContainsKey(CascadeKey) && Flag(body[CascadeKey], CascadeKey);
            return ask;
        }

        // expect 的键集合必须与 fields 完全相同；值是字符串或 null（null 按空串）。
        static void ParseExpect(SoDefinesAsk ask, Dictionary<string, object> expect)
        {
            foreach (string key in expect.Keys)
            {
                if (!ask.Values.ContainsKey(key))
                {
                    throw ArcReq.Bad(ExpectKey + "." + key + " 不在 " + FieldsKey + " 里", ExpectKey + "." + key);
                }
            }
            foreach (string key in ask.Keys)
            {
                if (!expect.ContainsKey(key))
                {
                    throw ArcReq.Bad(ExpectKey + " 必须带 " + key + "（当前为空用 null 或 \"\"）", ExpectKey + "." + key);
                }
                ask.Expect[key] = ExpectText(expect[key], ExpectKey + "." + key);
            }
        }

        static Dictionary<string, object> Map(Dictionary<string, object> body, string key)
        {
            Dictionary<string, object> map = Requests.Field(body, key) as Dictionary<string, object>;
            if (map == null)
            {
                throw ArcReq.Bad(key + " 必须是对象，键为 cdefine1 … cdefine16", key);
            }
            return map;
        }

        // 列键规范成小写；不是 cdefine1 … cdefine16、或规范后重复，400（field 为 at.原键）。
        static Dictionary<string, object> Normalise(Dictionary<string, object> map, string at)
        {
            Dictionary<string, object> result = new Dictionary<string, object>(StringComparer.Ordinal);
            foreach (KeyValuePair<string, object> pair in map)
            {
                string key = AsciiIgnoreCase.Fold(pair.Key);
                string label = FieldPath.Clean(at + "." + pair.Key);
                if (Number(key) == 0)
                {
                    throw ArcReq.Bad("未知字段 " + pair.Key + "（只认 cdefine1 … cdefine16）", label);
                }
                if (result.ContainsKey(key))
                {
                    throw ArcReq.Bad(at + " 里的 " + key + " 重复（列键不分大小写）", label);
                }
                result[key] = pair.Value;
            }
            return result;
        }

        // 小写列键的列号：cdefine1 … cdefine16 为 1 … 16，其余为 0。
        internal static int Number(string key)
        {
            if (key == null || !key.StartsWith(KeyPrefix, StringComparison.Ordinal))
            {
                return 0;
            }
            string digits = key.Substring(KeyPrefix.Length);
            if (digits.Length == 0 || digits.Length > 2 || digits[0] == '0')
            {
                return 0;
            }
            int n = 0;
            foreach (char ch in digits)
            {
                if (ch < '0' || ch > '9')
                {
                    return 0;
                }
                n = n * 10 + (ch - '0');
            }
            return n <= MaxColumn ? n : 0;
        }

        // 列键对应的 U8 列名（cdefine3 → cDefine3）。只用于已校验的列键，表名列名都不来自调用方原文。
        internal static string Column(string key)
        {
            return "cDefine" + Number(key).ToString(CultureInfo.InvariantCulture);
        }

        // empty_values：省略或 null 为空列表；否则是 0 到 EmptyMax 项字符串，每项去两端空白后 1 到 EmptyLength 个字符、不含控制字符。
        internal static string[] EmptyValues(object raw)
        {
            if (raw == null)
            {
                return new string[0];
            }
            IList list = raw as IList;
            if (list == null || raw is string)
            {
                throw ArcReq.Bad(EmptyKey + " 必须是字符串数组", EmptyKey);
            }
            if (list.Count > EmptyMax)
            {
                throw ArcReq.Bad(EmptyKey + " 最多 " + EmptyMax.ToString(CultureInfo.InvariantCulture) + " 项", EmptyKey);
            }
            string[] items = new string[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                items[i] = EmptyItem(list[i], EmptyKey + "." + i.ToString(CultureInfo.InvariantCulture));
            }
            return items;
        }

        static string EmptyItem(object raw, string label)
        {
            string text = raw as string;
            if (text == null)
            {
                throw ArcReq.Bad(label + " 必须是字符串", label);
            }
            text = text.Trim();
            if (text.Length == 0 || text.Length > EmptyLength)
            {
                throw ArcReq.Bad(label + " 去两端空白后须为 1 到 " + EmptyLength.ToString(CultureInfo.InvariantCulture) + " 个字符", label);
            }
            CheckText(text, label);
            return text;
        }

        // cascade：可省（省略即 false）；给了就必须是布尔值，显式 null 同样 400（与 API、客户端一致）。
        internal static bool Flag(object raw, string key)
        {
            if (!(raw is bool))
            {
                throw ArcReq.Bad(key + " 必须是 true 或 false", key);
            }
            return (bool)raw;
        }

        // 要写入的值：字符串，非空，前后没有空白，不超过 ValueMax，不含控制字符，不等于 empty_values 的任一项。
        internal static string Value(object raw, string label, string[] empty)
        {
            string text = raw as string;
            if (text == null)
            {
                throw ArcReq.Bad(label + " 必须是字符串", label);
            }
            if (text.Trim().Length == 0)
            {
                throw ArcReq.Bad(label + " 不能为空（本路由不清空列）", label);
            }
            if (text.Trim().Length != text.Length)
            {
                throw ArcReq.Bad(label + " 前后不能有空白", label);
            }
            CheckText(text, label);
            if (Array.IndexOf(empty, text) >= 0)
            {
                throw ArcReq.Bad(label + " 不能是 " + EmptyKey + " 里的「" + text + "」", label);
            }
            return text;
        }

        // expect 的值：null 与空串等价；不要求写法合规（已有数据可能带首尾空白），只挡超长和控制字符。
        internal static string ExpectText(object raw, string label)
        {
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

        // 空：null、空串、空白，或去两端空白后等于 empty_values 的某一项（区分大小写）。
        internal static bool Blank(string value, string[] empty)
        {
            string text = (value ?? "").Trim();
            return text.Length == 0 || Array.IndexOf(empty, text) >= 0;
        }

        // expect 比较：空值彼此等价；否则去两端空白后逐字相等。
        internal static bool Same(string a, string b, string[] empty)
        {
            if (Blank(a, empty) || Blank(b, empty))
            {
                return Blank(a, empty) && Blank(b, empty);
            }
            return string.Equals(a.Trim(), b.Trim(), StringComparison.Ordinal);
        }

        // 是否已是目标值：去两端空白后逐字相等（NULL 当空串）。
        internal static bool Exact(string a, string b)
        {
            return string.Equals((a ?? "").Trim(), (b ?? "").Trim(), StringComparison.Ordinal);
        }

        // 每个请求列都已是目标值。
        internal static bool AllExact(SoDefinesAsk ask, Dictionary<string, string> values)
        {
            foreach (string key in ask.Keys)
            {
                if (!Exact(Get(values, key), ask.Values[key]))
                {
                    return false;
                }
            }
            return true;
        }

        internal static string Get(Dictionary<string, string> values, string key)
        {
            string value;
            return values != null && values.TryGetValue(key, out value) ? value : null;
        }

        // 纯判断（自检覆盖）：先核对 expect，再挑出当前值不是目标值的列；一列都没有时 changed=false。
        internal static SoDefinesPlan Decide(SoDefinesAsk ask, SoDefinesSnap snap)
        {
            foreach (string key in ask.Keys)
            {
                if (!Same(ask.Expect[key], Get(snap.Values, key), ask.Empty))
                {
                    throw Mismatch("销售订单表头与 expect 不符：" + Describe(ask.Keys, snap.Values), ask.Keys, snap.Values);
                }
            }
            SoDefinesPlan plan = new SoDefinesPlan();
            foreach (string key in ask.Keys)
            {
                if (!Exact(Get(snap.Values, key), ask.Values[key]))
                {
                    plan.Set.Add(key);
                }
            }
            plan.Changed = plan.Set.Count > 0;
            return plan;
        }

        static string Describe(List<string> keys, Dictionary<string, string> values)
        {
            List<string> parts = new List<string>();
            foreach (string key in keys)
            {
                parts.Add(key + " " + Show(Get(values, key)));
            }
            return string.Join("，", parts.ToArray());
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

        // expect 不符：409 state_mismatch，detail.current 是请求各列的当前原值（NULL 为 null）。
        internal static BridgeException Mismatch(string message, List<string> keys, Dictionary<string, string> values)
        {
            return ArcCustomerRow.Mismatch(message, Current(keys, values));
        }

        internal static Dictionary<string, object> Current(List<string> keys, Dictionary<string, string> values)
        {
            Dictionary<string, object> current = new Dictionary<string, object>();
            foreach (string key in keys)
            {
                current[key] = Get(values, key);
            }
            return current;
        }

        internal static string Show(string value)
        {
            return value == null || value.Trim().Length == 0 ? "空" : value.Trim();
        }

        // 功能权限（销售订单变更，PermRegistrySoDefines）+ 表头数据权限（客户、部门、业务员、销售类型）。
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

        // 响应：changed 是表头有没有（会不会）变；current 是写后（预演和 changed=false 时是当前）的请求各列；ufts 是行版本。
        internal static Dictionary<string, object> Body(SoDefinesAsk ask, SoDefinesSnap snap, bool changed)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = Kind;
            body["id"] = ask.Id;
            body["code"] = snap.Code;
            body["changed"] = changed;
            body["current"] = Current(ask.Keys, snap.Values);
            body["ufts"] = snap.Ts;
            return body;
        }

        // 预演（validate）：检查都已在事务里做完；changed 是「真写会不会改」，current 是当前值。
        internal static Dictionary<string, object> DryBody(SoDefinesAsk ask, SoDefinesSnap snap, bool changed)
        {
            Dictionary<string, object> body = Body(ask, snap, changed);
            body["dry_run"] = true;
            body["mode"] = DryRunModes.Validate;
            return body;
        }
    }

    internal sealed class SoDefinesAsk
    {
        public int Id;
        // 请求列键（小写，按列号升序）。
        public List<string> Keys = new List<string>();
        // 列键 → 目标值。
        public Dictionary<string, string> Values = new Dictionary<string, string>(StringComparer.Ordinal);
        // 列键 → 调用方看到的当前值（null 已转成空串）。
        public Dictionary<string, string> Expect = new Dictionary<string, string>(StringComparer.Ordinal);
        // 另算作空的值（已去两端空白）。
        public string[] Empty = new string[0];
        // cascade=true：连带更新下游单据表头。
        public bool Cascade;
    }

    // 表头快照：Values 是请求各列的原值（NULL 为 null）。
    internal sealed class SoDefinesSnap
    {
        public string Code;
        public Dictionary<string, string> Values = new Dictionary<string, string>(StringComparer.Ordinal);
        public string Locker;
        public string Ts;
    }

    internal sealed class SoDefinesPlan
    {
        public bool Changed;
        // 要写的列键（当前值不是目标值的列，按列号升序）。
        public List<string> Set = new List<string>();
    }
}
