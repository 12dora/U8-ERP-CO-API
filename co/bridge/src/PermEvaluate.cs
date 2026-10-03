using System;
using System.Collections.Generic;

namespace U8Co
{
    // perm/evaluate：查另一个操作员（subject）在本账套、本年度的权限快照，输出同 perm/snapshot（PermSnapshot.ToJson）。
    // 只给 config.json 的 permEvaluateOperators 里的调用操作员（系统后台身份）；账套主管不因主管身份放行。名单缺省为空，即一律 403。
    // 调用操作员照常登录核对（读线程、登录缓存）；subject 只跑 PermLoad 的只读 SQL，不以 subject 登录 U8、不进权限缓存。
    // subject 在 UFSYSTEM..UA_User 里不存在或已停用（nState<>0）时 404。审计只记 subject、个数和指纹。
    internal static class PermEvaluate
    {
        internal const string Path = "/u8co/v1/perm/evaluate";
        internal const string Action = "perm_evaluate";
        internal const string RuleKey = "perm:evaluate";
        internal const string SubjectField = "subject";
        internal static readonly string[] Spec = new string[] { Path, SubjectField };

        const string UserSql = "SELECT TOP 1 CONVERT(varchar(10), ISNULL(u.nState, 0)) AS s FROM UFSYSTEM..UA_User u"
            + " WHERE u.cUser_Id=?";

        // 登录前和处理函数里各调一次，纯校验。规则同请求的 operator：1 到 20 个字符，不含空白、单引号、分号。
        public static string Parse(Dictionary<string, object> body)
        {
            object raw = body == null ? null : Requests.Field(body, SubjectField);
            string subject = raw as string;
            if (subject == null)
            {
                throw BridgeException.BadField(SubjectField, "缺少字段 subject");
            }
            if (!ConfigRules.OperatorCode(subject))
            {
                throw BridgeException.BadField(SubjectField, "subject 操作员编码无效");
            }
            return subject;
        }

        // 调用操作员必须在 permEvaluateOperators 里（不分大小写，同 U8 登录）。主管、数据权限管理员都不例外。
        public static void RequireCaller(BridgeConfig cfg, string caller)
        {
            string[] list = cfg == null || cfg.PermEvaluateOperators == null ? new string[0] : cfg.PermEvaluateOperators;
            for (int i = 0; i < list.Length; i++)
            {
                if (caller != null && string.Equals(list[i], caller, StringComparison.OrdinalIgnoreCase))
                {
                    return;
                }
            }
            throw new BridgeException(403, "no_permission", "该操作员不能查询其他操作员的权限");
        }

        // UA_User 的 nState：查不到（null）或不是 0（停用）一律 404，不给出快照。
        public static void RequireKnown(string state)
        {
            if (state == null || state.Trim() != "0")
            {
                throw new BridgeException(404, "not_found", "操作员不存在或已停用");
            }
        }

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Item == null || ctx.Item.Body == null)
            {
                throw new BridgeException(500, "internal", "缺少请求上下文");
            }
            WorkItem item = ctx.Item;
            string subject = Parse(item.Body);
            RequireCaller(item.Config ?? ctx.Config, item.Operator);
            // 调用操作员自己的快照（PermGate 已取），年度窗口与登录年度照搬给 subject。
            PermContext caller = PermCheck.Of(ctx);
            RequireKnown(Rows.Scalar(ctx.Conn, UserSql, new object[] { subject }));
            PermContext p = PermLoad.Load(ctx.Conn, caller.Acc, caller.Year, caller.DateYear, subject);
            Dictionary<string, object> body = PermSnapshot.ToJson(p);
            body["subject"] = subject;
            // 登录前已记 subject（Requests.ApplyPerm），这里只补个数和指纹。
            CoRows.Note(item, PermSnapshot.Summary(p, body));
            return ApiResult.Ok(body);
        }
    }

    // perm/snapshot、perm/evaluate 在 Requests.ApplyP4 里的登录前处理：不带 type、id，登录子系统 AS。
    // evaluate 在解密口令之后、登录 U8 之前先查 subject 格式和调用操作员名单，不在名单里就不占登录。字段表在 RequestsP4 的 P4Specs。
    internal static partial class Requests
    {
        // 处理了返回 true。
        static bool ApplyPerm(Dictionary<string, object> body, WorkItem item, string path)
        {
            if (path == PermSnapshot.Path)
            {
                item.SubId = "AS";
                return true;
            }
            if (path != PermEvaluate.Path)
            {
                return false;
            }
            string subject = PermEvaluate.Parse(body);
            item.SubId = "AS";
            PermEvaluate.RequireCaller(item.Config, item.Operator);
            CoRows.Note(item, "subject=" + subject);
            return true;
        }

        // 权限评估在登录前被拒（名单外 403）时，审计行的 detail 也要有 subject；格式不合法的不记（随后 400）。
        // 处理成功时 WorkRun 用 item.Detail（ApplyPerm 记的 subject + 个数和指纹）覆盖它。
        static void NoteSubject(AuditDraft draft, Dictionary<string, object> body, string path)
        {
            if (draft == null || path != PermEvaluate.Path)
            {
                return;
            }
            string subject = Field(body, PermEvaluate.SubjectField) as string;
            if (ConfigRules.OperatorCode(subject))
            {
                draft.Detail = "subject=" + subject;
            }
        }

        // 审计 action（ActionP4 调用）：字段说明 meta_fields、权限快照 perm_snapshot、权限评估 perm_evaluate；其他路由返回 null。
        static string MetaReadAction(string path)
        {
            if (path == MetaFieldsReq.Path)
            {
                return MetaFieldsReq.Action;
            }
            if (path == PermSnapshot.Path)
            {
                return PermSnapshot.Action;
            }
            return path == PermEvaluate.Path ? PermEvaluate.Action : null;
        }

        // 不带 type 的只读元数据路由（Typeless）：幂等结果查询、字段说明、权限快照与评估。
        static bool MetaReadPath(string path)
        {
            return path == IdemGet.Path || path == MetaFieldsReq.Path || PermSnapshot.Owns(path);
        }
    }
}
