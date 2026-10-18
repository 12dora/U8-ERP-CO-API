using System;
using System.Collections.Generic;

namespace U8Co
{
    // 客户专管业务员（archives/customer_person）：只改 Customer.cCusPPerson，以及符合规则时的分管部门 cCusDepart。
    // 不走 EAI：客户 diffedit 即使只带编码和业务员也会清银行账号、改最近收款日、补助记码，并拒无所属行业的客户。
    internal static class ArcPerson
    {
        public const string Op = "customer_person";
        public const string Path = Requests.ArcRoot + Op;
        internal const string RuleKey = "write:archive:customer:customer_person";
        const int PersonMax = 20;

        public static ArcPersonAsk Parse(Dictionary<string, object> body)
        {
            ArcPersonAsk ask = new ArcPersonAsk();
            ask.Code = ArcReq.CheckCode(Text(body, "code"), "code");
            ask.Person = ArcReq.CheckCode(Text(body, "person"), "person", PersonMax);
            object raw;
            if (body != null && body.TryGetValue("expect", out raw) && raw != null)
            {
                ask.HasExpect = true;
                ask.Expect = ExpectText(raw);
            }
            return ask;
        }

        public static ApiResult Run(WorkContext ctx, string op)
        {
            if (ctx == null || ctx.Item == null || ctx.Item.Body == null)
            {
                throw new BridgeException(500, "internal", "档案请求缺少请求体");
            }
            ArcPersonAsk ask = Parse(ctx.Item.Body);
            Permit(ctx, ask.Code);
            return ArcPersonSql.Run(ctx, ask);
        }

        static void Permit(WorkContext ctx, string code)
        {
            PermContext p = PermCheck.Of(ctx);
            PermCheck.RequireRule(p, PermRegistry.ForKey(RuleKey));
            if (!PermCheck.Allow(p, PermObj.Customer, code))
            {
                throw new BridgeException(403, "no_permission", PermCheck.DeniedRow);
            }
        }

        // cCusDepart 为空，或等于旧业务员所在部门时，改成新业务员的部门（新部门非空）。
        internal static bool FollowDept(string current, string oldDept, string newDept)
        {
            if (newDept == null || newDept.Length == 0)
            {
                return false;
            }
            if (current == null || current.Length == 0)
            {
                return true;
            }
            return string.Equals(current, oldDept ?? "", StringComparison.Ordinal);
        }

        // FollowDept 为真且 note 为空、新旧部门不同才真正改部门；note 非空写入 dept_note。
        // 部门值已经是目标时不改 cCusDepart，也不动 Customer_Auth。
        internal static void DecideFollow(ArcPersonSnap snap, string note)
        {
            if (snap == null || !FollowDept(snap.Dept, snap.OldDept, snap.NewDept))
            {
                return;
            }
            if (note != null && note.Length > 0)
            {
                snap.DeptNote = note;
                return;
            }
            if (string.Equals(snap.Dept ?? "", snap.NewDept ?? "", StringComparison.Ordinal))
            {
                return;
            }
            snap.Follow = true;
        }

        internal static string BlankNull(string text)
        {
            return text == null || text.Length == 0 ? null : text;
        }

        static string Text(Dictionary<string, object> body, string key)
        {
            object value = Requests.Field(body, key);
            if (value == null)
            {
                return null;
            }
            string text = value as string;
            if (text == null)
            {
                throw ArcReq.Bad(key + " 必须是字符串", key);
            }
            return text;
        }

        static string ExpectText(object raw)
        {
            string text = raw as string;
            if (text == null)
            {
                throw ArcReq.Bad("expect 必须是字符串", "expect");
            }
            if (text.Length > PersonMax)
            {
                throw ArcReq.Bad("expect 长度必须在 0 到 20 之间", "expect");
            }
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (c < ' ' || c == '\x7f')
                {
                    throw ArcReq.Bad("expect 含有控制字符", "expect");
                }
            }
            return text;
        }

        internal static Dictionary<string, object> OkBody(ArcPersonAsk ask, ArcPersonSnap snap, bool wrote)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["code"] = ask.Code;
            body["before"] = BlankNull(snap.Person);
            body["after"] = wrote ? ask.Person : BlankNull(snap.Person);
            body["dept_before"] = BlankNull(snap.Dept);
            string deptAfter = wrote && snap.Follow ? snap.NewDept : snap.Dept;
            body["dept_after"] = BlankNull(deptAfter);
            body["dept_note"] = BlankNull(snap.DeptNote);
            return body;
        }

        internal static Dictionary<string, object> DryBody(ArcPersonAsk ask, ArcPersonSnap snap)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["dry_run"] = true;
            body["mode"] = DryRunModes.Validate;
            body["code"] = ask.Code;
            body["before"] = BlankNull(snap.Person);
            body["dept_before"] = BlankNull(snap.Dept);
            body["dept_note"] = BlankNull(snap.DeptNote);
            return body;
        }
    }

    internal sealed class ArcPersonAsk
    {
        public string Code;
        public string Person;
        public bool HasExpect;
        public string Expect;
    }

    internal sealed class ArcPersonSnap
    {
        public string Person;
        public string Dept;
        public string Ts;
        public string OldDept;
        public string NewDept;
        public string PersonCode;
        public bool Follow;
        public string DeptNote;
    }
}
