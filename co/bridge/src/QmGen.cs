using System;
using System.Collections.Generic;

namespace U8Co
{
    // 质量单据写路由的入口（Dispatch 分派）：生单（报检单 QmGenInspect、检验单 QmGenCheck）、删除 QmDelete（报检单删除前的弃审 QmVerify）。
    // 登录子系统 QM（RequestsP4 经 QmReq.Check 设定）。见 docs/u8-notes.md「质量单据新增」。
    internal static class QmGen
    {
        public static bool Handles(VoucherKind kind)
        {
            return QmSpec.Handles(kind);
        }

        public static ApiResult Generate(WorkContext ctx, VoucherKind kind, int sourceId, Dictionary<string, object> head,
            object[] lines)
        {
            QmSpec spec = Need(kind);
            if (spec.Inspect)
            {
                return QmGenInspect.Run(ctx, spec, sourceId, head, lines);
            }
            return QmGenCheck.Run(ctx, spec, sourceId, head, lines);
        }

        public static ApiResult Delete(WorkContext ctx, VoucherKind kind, int id)
        {
            return QmDelete.Run(ctx, Need(kind), id);
        }

        static QmSpec Need(VoucherKind kind)
        {
            QmSpec spec = QmSpec.Of(kind);
            if (spec == null)
            {
                throw new BridgeException(400, "bad_request", "该单据类型不是质量单据");
            }
            return spec;
        }

        // 功能权限（写路由每次现读，不走缓存）。返回权限快照给数据权限判断用。
        internal static PermContext RequireRule(WorkContext ctx, QmSpec spec, string op)
        {
            PermContext perm = PermCheck.Of(ctx);
            PermCheck.RequireRule(perm, PermRegistry.ForKey(spec.RuleKey(op)));
            return perm;
        }

        internal static void CheckRows(PermContext perm, QmSpec spec, string op, List<Dictionary<string, object>> rows)
        {
            MoDelete.CheckRows(perm, PermRegistry.ForKey(spec.RuleKey(op)), rows);
        }

        // 事务里的 add 失败：U8 已自行结束事务（@@TRANCOUNT 为 0）时回滚不了，504；否则调用方回滚后 409 u8_rejected，
        // 原文带回（ErrBag / error 串，或其他 COM 异常的第一行）。
        internal static void Refuse(WorkContext ctx, QmOutcome outcome)
        {
            if (outcome.Ok)
            {
                return;
            }
            CoRows.Note(ctx.Item, "VoucherOperate " + (outcome.Error.Length > 0 ? outcome.Error : Line(outcome.Lost)));
            if (ctx.Item.TranAfter == "0")
            {
                throw new BridgeException(504, "outcome_unknown", "U8 报错且已自行结束事务，结果未知：" + Text(outcome));
            }
            throw new BridgeException(409, "u8_rejected", Text(outcome));
        }

        // 自己提交的调用（检验单新增、删除、审核、弃审）之后，请求连接上不能留着事务（CHECKLIST「事务」）。留下了就回滚、记审计，
        // 返回 true，由调用方报 504（在新连接上回读会被这个事务锁住，连接回池后也会被下一个请求带走）。
        internal static bool TranLeft(WorkContext ctx)
        {
            string left;
            try
            {
                left = CoTrans.Count(ctx.Conn);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "@@TRANCOUNT " + ex.Message);
                return true;
            }
            ctx.Item.TranAfter = left;
            if (left == "0")
            {
                return false;
            }
            CoRows.Note(ctx.Item, "U8 调用后 @@TRANCOUNT=" + left + "，已回滚");
            try
            {
                CoTrans.Rollback(ctx.Conn);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "回滚失败 " + ex.Message);
            }
            return true;
        }

        internal static string Text(QmOutcome outcome)
        {
            return outcome.Error.Length > 0 ? outcome.Error : Line(outcome.Lost);
        }

        internal static string Line(Exception ex)
        {
            if (ex == null || ex.Message == null)
            {
                return "";
            }
            string text = ex.Message.Trim();
            int nl = text.IndexOfAny(new char[] { '\r', '\n' });
            return nl > 0 ? text.Substring(0, nl) : text;
        }
    }
}
