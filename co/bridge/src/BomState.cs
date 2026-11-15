using System;
using System.Collections.Generic;

namespace U8Co
{
    // 物料清单审核 / 弃审（vouchers/verify）和删除（vouchers/delete），type=bom，id=BomId。
    // U8API BomAuditing / BomUnauditing / BomDelete，参数是（母件 PartId、1、版本号字符串），登录子系统 BO，不包 CoTrans。
    // 调用前按表头挡住：审批流控制的（IsWFControlled=1）409 workflow_enabled；审核只收 Status=1，弃审只收 Status=3；
    // 删除只收 Status=1，且没有生产订单、委外订单、组装 / 拆卸 / 形态转换单、配比出库单、调拨单的 BomId 指向它（U8 不拦生产订单，实测 B6）。
    // 弃审时 U8 不查生产订单是否在用（BOStateProcess 没有这道闸门），桥也不拦，交给 U8。
    // 调用后在新连接上回读状态 / 是否还在，到了目标才算成功。
    internal static class BomState
    {
        public static ApiResult Verify(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            if (action != "verify" && action != "unverify")
            {
                throw new BridgeException(400, "bad_request", "action 只能是 verify 或 unverify");
            }
            bool verify = action == "verify";
            PermContext perm = PermCheck.Of(ctx);
            PermRule rule = PermRegistry.ForKey(verify ? BomRoutes.VerifyRule : BomRoutes.UnverifyRule);
            PermCheck.RequireRule(perm, rule);
            Dictionary<string, object> head = BomRead.Head(ctx.Conn, id);
            Gate(head, verify);
            MoDelete.CheckRows(perm, rule, BomRoutes.ParentRows(CoRows.Col(head, "inv_code")));
            string what = verify ? "审核" : "弃审";
            Exception lost = BomCom.Triple(ctx, verify ? BomCom.AuditUrl : BomCom.UnauditUrl, PartOf(head),
                CoRows.Col(head, "version"));
            Dictionary<string, object> after = BomOut.Reread(ctx, id, what);
            if (CoRows.Col(after, "status") != (verify ? "3" : "1"))
            {
                throw BomOut.Unreached(lost, what, id);
            }
            Dictionary<string, object> body = BomRead.Body(kind, id, after);
            body["action"] = action;
            return ApiResult.Ok(body);
        }

        public static ApiResult Delete(WorkContext ctx, VoucherKind kind, int id)
        {
            PermContext perm = PermCheck.Of(ctx);
            PermRule rule = PermRegistry.ForKey(BomRoutes.DeleteRule);
            PermCheck.RequireRule(perm, rule);
            Dictionary<string, object> head = BomRead.Head(ctx.Conn, id);
            Open(head, "删除");
            BomSql.CheckUnused(ctx.Conn, id);
            MoDelete.CheckRows(perm, rule, BomRoutes.ParentRows(CoRows.Col(head, "inv_code")));
            Exception lost = BomCom.Triple(ctx, BomCom.DeleteUrl, PartOf(head), CoRows.Col(head, "version"));
            if (!Gone(ctx, id))
            {
                throw BomOut.Unreached(lost, "删除", id);
            }
            Dictionary<string, object> body = BomRead.Body(kind, id, head);
            body.Remove("state");
            body["deleted"] = true;
            return ApiResult.Ok(body);
        }

        // 修改、删除只收未审核（Status=1）、没进审批流的。
        internal static void Open(Dictionary<string, object> head, string what)
        {
            Workflow(head);
            string status = CoRows.Col(head, "status");
            if (status == "4")
            {
                throw new BridgeException(409, "state_mismatch", "已停用的物料清单不能" + what);
            }
            if (status != "1")
            {
                throw new BridgeException(409, "state_mismatch", "已审核的物料清单不能" + what);
            }
        }

        static void Gate(Dictionary<string, object> head, bool verify)
        {
            Workflow(head);
            string status = CoRows.Col(head, "status");
            if (status == "4")
            {
                throw new BridgeException(409, "state_mismatch", "物料清单已停用");
            }
            if (verify && status != "1")
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            if (!verify && status != "3")
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
        }

        // 审批流控制的物料清单 API 会走提交，本期不支持（同生产订单）。
        static void Workflow(Dictionary<string, object> head)
        {
            if (CoRows.Col(head, "wf") == "1")
            {
                throw new BridgeException(409, "workflow_enabled", "单据已启用审批流，本期不支持");
            }
        }

        static int PartOf(Dictionary<string, object> head)
        {
            int part = CoRows.AsId(CoRows.Col(head, "part_id"));
            if (part <= 0)
            {
                throw new BridgeException(500, "internal", "物料清单缺少母件物料");
            }
            return part;
        }

        static bool Gone(WorkContext ctx, int id)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                return BomSql.Gone(conn, id);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "BomState " + MoApi.FirstLine(ex.Message));
                throw new BridgeException(504, "outcome_unknown", "已提交删除但未能回读，BomId " + BomReq.Num(id));
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }
    }
}
