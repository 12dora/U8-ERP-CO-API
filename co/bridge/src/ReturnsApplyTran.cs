using System;
using System.Collections.Generic;

namespace U8Co
{
    // K12 退货申请单（VT 34、卡片 SA31）写入的事务。VoucherCO_SA 对这张卡片的 Save、Delete、VerifyVouch 转给 .NET 的
    // SaVoucherService.clsSaVoucherService（SaveVoucher / DeleteVoucher / VerifyVoucher / unVerifyVoucher），它在传入的
    // ADO 连接上自己 BeginTrans / Commit，不看 bManualTrans（实测：桥包 CoTrans 时 Save 报
    // 「SaveVoucher:无法在此会话中启动更多的事务。」）。所以 VT 34 的写都不包 CoTrans，按自行提交的组件处理：
    // 检查都在调用前做完（不能跨调用持锁），调用前 DryRun.Stop；调用抛错、调用后连接上仍有事务都 504 outcome_unknown；
    // U8 返回非空串 409 u8_rejected（组件已回滚）；之后的核对经 StockCall.AfterCheck（事务已结束，核对失败一律 504）。
    internal static class ReturnsApplyTran
    {
        internal const string What = "销售 CO 退货申请单（SaVoucherService 自行提交）";

        // state：0 新增，1 修改。返回 Save 的实参（args[3] 是新主键）。
        internal static object[] Save(WorkContext ctx, object co, object[] doms, short state, string label)
        {
            object[] args = new object[] { doms[0], doms[1], state, "" };
            Run(ctx, co, "Save", args, new int[] { 3 }, label);
            return args;
        }

        // 每行 editprop=D 后 Delete(头, 体)，by-ref {0,1}。
        internal static void Delete(WorkContext ctx, object co, object[] doms, string label)
        {
            List<object> rows = DomRows.RowsOf(doms[1]);
            for (int i = 0; i < rows.Count; i++)
            {
                DomRows.Set(doms[1], rows[i], "editprop", "D");
            }
            object[] args = new object[] { doms[0], doms[1] };
            Run(ctx, co, "Delete", args, new int[] { 0, 1 }, label);
            CoRows.Swap(doms, 0, args[0]);
            CoRows.Swap(doms, 1, args[1]);
        }

        internal static void Verify(WorkContext ctx, object co, object head, bool on, string label)
        {
            Run(ctx, co, "VerifyVouch", new object[] { head, on }, null, label);
        }

        static void Run(WorkContext ctx, object co, string method, object[] args, int[] refs, string label)
        {
            if (CoTrans.Count(ctx.Conn) != "0")
            {
                throw new BridgeException(500, "internal", "请求连接上有未结束的事务");
            }
            DryRun.Stop(ctx, What + " " + method);
            ctx.Item.TranBefore = "0";
            object ret;
            try
            {
                ret = refs == null ? ComUtil.Call(co, method, args) : ComUtil.CallRef(co, method, args, refs);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, method + " " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "U8 处理退货申请单时出错，结果未知，需要人工核对：" + label);
            }
            ctx.Item.TranAfter = Count(ctx.Conn);
            string msg = ret == null ? "" : Convert.ToString(ret);
            CoRows.Note(ctx.Item, method + " " + (msg.Length == 0 ? "ok" : msg));
            // 组件应已提交或回滚；调用后连接上仍有事务（或读不到层数）就说不清写没写进去。
            if (ctx.Item.TranAfter != "0")
            {
                CoRows.Note(ctx.Item, method + " 调用后事务层数 " + ctx.Item.TranAfter);
                throw new BridgeException(504, "outcome_unknown", "U8 处理退货申请单后事务未结束，结果未知，需要人工核对：" + label);
            }
            if (msg.Length > 0)
            {
                throw new BridgeException(409, "u8_rejected", msg);
            }
        }

        static string Count(object conn)
        {
            try
            {
                return CoTrans.Count(conn);
            }
            catch (Exception)
            {
                return "?";
            }
        }
    }
}
