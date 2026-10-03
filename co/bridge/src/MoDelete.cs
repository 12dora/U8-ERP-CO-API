using System;
using System.Collections.Generic;

namespace U8Co
{
    // 生产订单删除（vouchers/delete，type=production_order，id=MoId）：U8API MOrderDelete（参数 mocode），登录子系统 MO。
    // API 自己开 TransactionScope 提交，不包 CoTrans。调用前查完闸门：集合生产订单（CollectiveFlag 非 0）不删，
    // 行状态只能是 1 / 2（未审核），
    // 审批流控制且已提交的拒绝，已被材料出库（iMPoIds = 子件 AllocateId）或产成品入库（iMPoIds = 行 MoDId）引用的拒绝。
    // 调用后在新连接上确认 mom_order 已没有这张订单。见 docs/u8-notes.md「生产订单新增与删除」。
    internal static class MoDelete
    {
        public const string DeleteRule = "write:production_order:delete";
        const string DeleteUrl = "U8API/MOrder/MOrderDelete";
        const string RowsSql = "select o.MoCode, convert(varchar(20), d.MoDId) as MoDId, convert(varchar(10), d.Status) as Status,"
            + " convert(varchar(10), isnull(d.IsWFControlled,0)) as wf, convert(varchar(10), isnull(d.iVerifyState,0)) as vs,"
            + " convert(varchar(10), isnull(d.CollectiveFlag,0)) as cf, d.InvCode, d.MDeptCode, d.WhCode"
            + " from mom_order o join mom_orderdetail d on d.MoId=o.MoId where o.MoId=? order by d.SortSeq, d.MoDId";
        const string DownSql = "select top 1 x.kind from ("
            + "select 'material_out' as kind from rdrecords11 b join mom_moallocate a on a.AllocateId=b.iMPoIds"
            + " join mom_orderdetail d on d.MoDId=a.MoDId where d.MoId=?"
            + " union all select 'product_in' as kind from rdrecords10 b join mom_orderdetail d on d.MoDId=b.iMPoIds"
            + " where d.MoId=?) x order by x.kind";
        const string GoneSql = "select count(*) as n from mom_order where MoId=?";

        public static ApiResult Run(WorkContext ctx, VoucherKind kind, int id)
        {
            U8Resolve.Enter();
            try
            {
                return Core(ctx, kind, id);
            }
            finally
            {
                U8Resolve.Leave();
            }
        }

        static ApiResult Core(WorkContext ctx, VoucherKind kind, int id)
        {
            if (kind == null || kind.Name != "production_order")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持删除");
            }
            PermContext perm = PermCheck.Of(ctx);
            PermRule rule = PermRegistry.ForKey(DeleteRule);
            PermCheck.RequireRule(perm, rule);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, RowsSql, new object[] { id }, 5000);
            if (rows.Count == 0)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            Gate(ctx.Conn, id, rows);
            CheckRows(perm, rule, rows);
            string code = CoRows.Col(rows[0], "MoCode");
            // 预演（校验模式）：MOrderDelete 自己提交，闸门查完就停。
            DryRun.Stop(ctx, "U8API MOrderDelete");
            Exception lost = MoApi.Invoke(ctx, DeleteUrl, code);
            Confirm(ctx, id, code, lost);
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["code"] = code;
            body["deleted"] = true;
            return ApiResult.Ok(body);
        }

        // U8 的 Usp_MO_Del 只删 Status 1 / 2 的行；审批流控制且已提交（iVerifyState=1）在 BE 里就拒绝。
        static void Gate(object conn, int id, List<Dictionary<string, object>> rows)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                if (CoRows.Col(rows[i], "cf") != "0")
                {
                    throw new BridgeException(409, "state_mismatch", "集合生产订单请在 U8 客户端删除");
                }
                string status = CoRows.Col(rows[i], "Status");
                if (status != "1" && status != "2")
                {
                    throw new BridgeException(409, "state_mismatch", "已审核或关闭的生产订单不能删除");
                }
            }
            for (int i = 0; i < rows.Count; i++)
            {
                if (CoRows.Col(rows[i], "wf") == "1" && CoRows.Col(rows[i], "vs") == "1")
                {
                    throw new BridgeException(409, "workflow_enabled", "生产订单已提交审批，不能删除");
                }
            }
            string down = Rows.Scalar(conn, DownSql, new object[] { id, id });
            if (down == "material_out")
            {
                throw new BridgeException(409, "state_mismatch", "生产订单已被材料出库单引用，不能删除");
            }
            if (down == "product_in")
            {
                throw new BridgeException(409, "state_mismatch", "生产订单已被产成品入库单引用，不能删除");
            }
        }

        // 每一行都要在操作员的数据权限内（存货、部门、仓库；与生产订单关闭同一套对象）。新增按请求的行判断。
        internal static void CheckRows(PermContext perm, PermRule rule, List<Dictionary<string, object>> rows)
        {
            if (perm == null || perm.Supervisor || rule == null)
            {
                return;
            }
            for (int r = 0; r < rows.Count; r++)
            {
                for (int i = 0; i < rule.Objs.Length; i++)
                {
                    PermObj o = rule.Objs[i];
                    if (perm.Controls(o.Obj) && !PermCheck.RowOk(perm, o, rows[r]))
                    {
                        throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
                    }
                }
            }
        }

        // 回读确认订单已不在。没删掉时：调用有 IPC 错误报 503，其他异常结果未知（504），U8 返回成功却还在报 409。
        static void Confirm(WorkContext ctx, int id, string code, Exception lost)
        {
            if (Gone(ctx, id, code))
            {
                return;
            }
            if (lost != null && MoApi.IsIpc(lost.Message))
            {
                throw new BridgeException(503, "u8_unavailable", "U8 生产制造服务未运行");
            }
            if (lost != null)
            {
                string text = MoApi.FirstLine(lost.Message);
                throw new BridgeException(504, "outcome_unknown",
                    "U8 删除调用异常，结果未知，生产订单 " + code + (text.Length > 0 ? "：" + text : ""));
            }
            throw new BridgeException(409, "state_mismatch", "U8 返回成功但生产订单仍在");
        }

        static bool Gone(WorkContext ctx, int id, string code)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                string n = Rows.Scalar(conn, GoneSql, new object[] { id });
                return n != null && n.Trim() == "0";
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "MoDelete " + MoApi.FirstLine(ex.Message));
                throw new BridgeException(504, "outcome_unknown", "已提交删除但未能回读，生产订单 " + code);
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }
    }
}
