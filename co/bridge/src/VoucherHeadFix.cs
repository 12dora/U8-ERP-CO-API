using System;
using System.Collections.Generic;

namespace U8Co
{
    // 历史发货单表头修正（vouchers/head_fix）：只改已审核发货单表头的收货地址编码 caddcode、业务员 cPersonCode，
    // 并让已生成的销售出库单、销售发票（及发票在 Ar_Detail 里的业务员）跟着一致。U8 界面不允许改已审核且有下游单据的发货单，
    // 这条路由是受控的例外：不碰金额、数量、日期、科目、总账，不碰 cShipAddress、修改人、审核人，也不走 EditMore / SaleEdit。
    // 一个事务里 UPDLOCK 锁发货单表头，校验 expect / 地址 / 业务员，算出下游每张单据能不能跟着改（HeadFixPlan，纯判断），
    // 预演到此回滚，否则逐张 UPDATE，回读核对。单据锁与修改发货单相同（"dispatch:<id>"，DocLocks 按 type + id 派生）。
    // 回滚：fields 与 expect 对调，并把正向响应的 undo 原样带回——回滚只动 undo 列出的行（各行当前值必须等于本次 expect，
    // 且必须属于这张发货单），不再发现一遍，所以兄弟发货单、已经是新值的下游行、Ar_Detail 里本来就是新业务员的行都不会被误改。
    // 不带 undo 时按规则发现下游（可能与正向不对称，只作兜底）。值按 U8 的写法归一：去两端空格、不分大小写比较、空串写 NULL，
    // 地址与业务员写成档案里的写法。当前业务员为空时不能改业务员（409 person_empty）。
    // 单据锁："dispatch:<id>" 与 "sale_return:<id>"（红字发货单是退货单类型，发货单与退货单同表同 id 空间，两把都锁）。
    internal static class VoucherHeadFix
    {
        public const string Op = "head_fix";
        public const string Path = "/u8co/v1/vouchers/" + Op;
        public const string Kind = "dispatch";
        public const string AddrKey = "addr_code";
        public const string PersonKey = "person_code";
        internal const string RuleKey = "write:voucher:dispatch:head_fix";
        // 登录前字段表（Requests.RouteSpecs）：路径加除公共字段外的顶层键。
        internal static readonly string[] Spec = new string[] { Path, "type", "id", "fields", "expect", "undo" };
        const int AddrMax = 30;
        const int PersonMax = 20;
        const int ExpectMax = 255;

        // 登录前的类型闸门（Requests.KeyGuards）：v1 只认发货单。
        public static void GuardKind(VoucherKind kind)
        {
            if (kind == null || kind.Name != Kind)
            {
                throw new BridgeException(400, "bad_request", "历史表头修正目前只支持 type=" + Kind, "type");
            }
        }

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Item == null || ctx.Item.Body == null)
            {
                throw new BridgeException(500, "internal", "表头修正请求缺少请求体");
            }
            GuardKind(ctx.Item.Type);
            HeadFixAsk ask = Parse(ctx.Item.Body);
            ask.Id = ctx.Item.Id;
            return VoucherHeadFixSql.Run(ctx, ask);
        }

        // 校验 fields / expect（id、type 已由 Requests 校验）。至少一个字段；expect 与 fields 的键必须一一对应。
        public static HeadFixAsk Parse(Dictionary<string, object> body)
        {
            HeadFixAsk ask = new HeadFixAsk();
            Dictionary<string, object> fields = Obj(Requests.Field(body, "fields"), "fields");
            Dictionary<string, object> expect = Obj(Requests.Field(body, "expect"), "expect");
            if (fields == null || fields.Count == 0)
            {
                throw ArcReq.Bad("没有要修改的字段", "fields");
            }
            CheckKeys(fields, "fields");
            CheckKeys(expect, "expect");
            AddField(ask, fields, expect, AddrKey, 0, AddrMax);
            AddField(ask, fields, expect, PersonKey, 1, PersonMax);
            HeadFixUndo.Parse(body, ask);
            return ask;
        }

        // 键只能是 addr_code、person_code；多余的键 400 并带键名。
        static void CheckKeys(Dictionary<string, object> map, string label)
        {
            if (map == null)
            {
                return;
            }
            foreach (string key in map.Keys)
            {
                if (key != AddrKey && key != PersonKey)
                {
                    throw ArcReq.Bad("未知字段 " + key, FieldPath.Clean(label + "." + key));
                }
            }
        }

        static void AddField(HeadFixAsk ask, Dictionary<string, object> fields, Dictionary<string, object> expect,
            string key, int min, int max)
        {
            bool wanted = fields.ContainsKey(key);
            if (!wanted)
            {
                if (expect != null && expect.ContainsKey(key))
                {
                    throw ArcReq.Bad("expect 里的 " + key + " 不在 fields 中", "expect." + key);
                }
                return;
            }
            if (expect == null || !expect.ContainsKey(key))
            {
                throw ArcReq.Bad("修改 " + key + " 必须在 expect 里带当前值（空用 \"\"）", "expect." + key);
            }
            HeadFixField f = new HeadFixField();
            f.Key = key;
            f.Column = key == AddrKey ? "caddcode" : "cPersonCode";
            f.New = Value(fields[key], "fields." + key, min, max);
            // expect 是读到的当前值，两端的空格不算（U8 的 char 列可能带填充）。
            string current = expect[key] as string;
            f.Expect = Value(current == null ? expect[key] : current.Trim(), "expect." + key, 0, ExpectMax);
            ask.Fields.Add(f);
        }

        // 字符串，长度 min..max，前后没有空格，不含控制字符。
        static string Value(object raw, string label, int min, int max)
        {
            string text = raw as string;
            if (text == null)
            {
                throw ArcReq.Bad(label + " 必须是字符串（空用 \"\"）", FieldPath.Clean(label));
            }
            if (text.Length < min)
            {
                throw ArcReq.Bad(label + " 不能为空", FieldPath.Clean(label));
            }
            if (text.Length > max)
            {
                throw ArcReq.Bad(label + " 长度不能超过 " + max.ToString(System.Globalization.CultureInfo.InvariantCulture),
                    FieldPath.Clean(label));
            }
            if (text.Trim().Length != text.Length)
            {
                throw ArcReq.Bad(label + " 前后不能有空格", FieldPath.Clean(label));
            }
            for (int i = 0; i < text.Length; i++)
            {
                if (text[i] < ' ' || text[i] == '\x7f')
                {
                    throw ArcReq.Bad(label + " 含有控制字符", FieldPath.Clean(label));
                }
            }
            return text;
        }

        static Dictionary<string, object> Obj(object value, string label)
        {
            if (value == null)
            {
                return null;
            }
            Dictionary<string, object> map = value as Dictionary<string, object>;
            if (map == null)
            {
                throw ArcReq.Bad(label + " 必须是对象", FieldPath.Clean(label));
            }
            return map;
        }

        // 单据锁键：与修改发货单相同的 "dispatch:<id>"，另加 "sale_return:<id>"（DocLocks 排程时还不知道是蓝字还是红字）。
        internal static string[] LockKeys(int id)
        {
            string text = id.ToString(System.Globalization.CultureInfo.InvariantCulture);
            return new string[] { Kind + ":" + text, "sale_return:" + text };
        }

        // 功能权限（发货单修改）+ 表头数据权限（客户、部门、原业务员）+ 新业务员的数据权限。
        internal static void Permit(WorkContext ctx, Dictionary<string, object> head, HeadFixAsk ask)
        {
            PermContext p = PermCheck.Of(ctx);
            PermRule rule = PermRegistry.ForKey(RuleKey);
            PermCheck.RequireRule(p, rule);
            HeadFixField person = ask.Find(PersonKey);
            bool ok = PermCheck.RowAllowed(p, rule, head)
                && (person == null || PermCheck.Allow(p, PermObj.Person, person.New));
            if (!ok)
            {
                throw new BridgeException(403, "no_permission", PermCheck.DeniedRow);
            }
        }

        // 响应。changed 是发货单表头有没有（会不会）变；before / after 只含请求里的字段。
        internal static Dictionary<string, object> Body(HeadFixAsk ask, string code, bool changed, List<HeadFixRow> plan)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = Kind;
            body["id"] = ask.Id;
            body["code"] = code;
            body["changed"] = changed;
            Dictionary<string, object> before = new Dictionary<string, object>();
            Dictionary<string, object> after = new Dictionary<string, object>();
            for (int i = 0; i < ask.Fields.Count; i++)
            {
                before[ask.Fields[i].Key] = ask.Fields[i].Old;
                after[ask.Fields[i].Key] = ask.Fields[i].Changes ? ask.Fields[i].New : ask.Fields[i].Old;
            }
            body["before"] = before;
            body["after"] = after;
            List<object> down = new List<object>();
            for (int i = 0; plan != null && i < plan.Count; i++)
            {
                down.Add(plan[i].Map());
            }
            body["downstream"] = down;
            // 实际写过的行：回滚时原样放进请求的 undo（HeadFixUndo）。
            body["undo"] = HeadFixUndo.Map(plan);
            return body;
        }

        // 预演（validate）：检查和下游计划都已在事务里做完；changed 是「真写会不会改」。
        internal static Dictionary<string, object> DryBody(HeadFixAsk ask, string code, bool changed, List<HeadFixRow> plan)
        {
            Dictionary<string, object> body = Body(ask, code, changed, plan);
            body["dry_run"] = true;
            body["mode"] = DryRunModes.Validate;
            return body;
        }
    }
}
