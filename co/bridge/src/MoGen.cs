using System;
using System.Collections;
using System.Collections.Generic;
using System.Data;

namespace U8Co
{
    // 生产订单参照销售订单生成（vouchers/generate，type=production_order，source_type=sale_order）：ATO 模型存货的
    // 销售订单行 → 一张标准生产订单（OrdFlag=1，有客户 BOM 按客户 BOM 展开，否则标准 BOM）。走 U8 界面
    // 「生产订单生成（参照销售订单）」MO05001 同一套组件（MoGenU8）：先取 U8 的生单列表（只取这张销售订单），
    // 按请求改生单数量、生产部门、类别，再交给 U8 保存。U8 按行各开 RequiresNew 事务提交（取号、Usp_MO_GenMoFromSo、
    // 回写销售订单已下达数量、ATO 净需求），不包 CoTrans，也可能只成功一部分；调用后在新连接上逐行核对（MoGenCheck）。
    // 登录子系统 MO。见 docs/u8-notes.md「参照销售订单生成（ATO）」。
    internal static class MoGen
    {
        public const string GenRule = "write:production_order:generate";

        // 来源 id（SO_SOMain.ID）、表头、表体取自 ctx.Item（Requests 已解析，MoGenReq 已在登录前校验）。
        public static ApiResult Run(WorkContext ctx, VoucherKind kind)
        {
            WorkItem item = ctx.Item;
            Dictionary<string, object> head = item.Head ?? new Dictionary<string, object>();
            object[] lines = item.Lines ?? new object[0];
            U8Resolve.Enter();
            try
            {
                return Core(ctx, kind, item.Id, head, lines);
            }
            finally
            {
                U8Resolve.Leave();
            }
        }

        static ApiResult Core(WorkContext ctx, VoucherKind kind, int soId, Dictionary<string, object> head, object[] lines)
        {
            if (kind == null || kind.Name != "production_order")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持生单");
            }
            MoGenReq.CheckSource(ctx.Item);
            MoGenAsk ask = MoGenReq.Parse(soId, head, lines);
            string user = MoCreate.Operator(ctx);
            PermContext perm = PermCheck.Of(ctx);
            ask.DataAuth = perm != null && !perm.Supervisor;
            PermRule rule = PermRegistry.ForKey(GenRule);
            PermCheck.RequireRule(perm, rule);
            // 先查来源、编码（不存在 400、状态不符 409），再取 U8 列表，最后按数据权限判断（越权 403）。
            MoGenSql.Validate(ctx.Conn, ask);
            if (ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "没有 U8 登录");
            }
            using (MoGenU8 u8 = MoGenU8.Open(ctx.Session.Token))
            {
                MoGenArgs.Attach(ask, List(ctx, u8, ask));
                MoDelete.CheckRows(perm, rule, PermRows(ask));
                MoGenSql.Mark(ctx.Conn, ask);
                // 预演（校验模式）：U8 按行自己提交，闸门查完就停。
                MoGenOut.DryInput(ask);
                DryRun.Stop(ctx, "U8 生产订单生成");
                MoGenOut.LogSnapshot(ctx, ask);
                MoGenCall call = Save(ctx, u8, ask);
                return MoGenCheck.Settle(ctx, kind, ask, user, call);
            }
        }

        // 列表只读，出错时什么都没写：IPC 错误 503，其余按 U8 拒绝（MoApi.Failed）。
        static DataTable List(WorkContext ctx, MoGenU8 u8, MoGenAsk ask)
        {
            try
            {
                DateTime[] range = u8.Range();
                return u8.List(MoGenArgs.ListArgs(ask, range[0], range[1]));
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "MoGen 列表 " + ex.GetType().Name + " " + MoApi.FirstLine(ex.Message));
                throw MoApi.Failed(ex);
            }
        }

        // 数据权限按存货、最终的生产部门判断（与生产订单新增同一套对象；仓库为空）。类别的数据权限由 U8 自己判（Cnt 901028）。
        static List<Dictionary<string, object>> PermRows(MoGenAsk ask)
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            for (int i = 0; i < ask.Lines.Count; i++)
            {
                Dictionary<string, object> row = new Dictionary<string, object>();
                row["InvCode"] = ask.Lines[i].InvCode;
                row["MDeptCode"] = ask.Lines[i].FinalDept;
                row["WhCode"] = "";
                rows.Add(row);
            }
            return rows;
        }

        // 拼参数、取制单信息出错时还没调用 U8，照常报；调用开始后的任何异常（含 IPC）交给 MoGenCheck 按库确认，
        // 不报 503（U8 按行提交，通道断了也可能还在提交）。
        static MoGenCall Save(WorkContext ctx, MoGenU8 u8, MoGenAsk ask)
        {
            Hashtable args;
            try
            {
                args = MoGenArgs.SaveArgs(ask, u8.Who());
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                throw MoApi.Failed(ex);
            }
            MoGenCall call = new MoGenCall();
            try
            {
                call.Result = u8.Save(args);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "MoGen " + ex.GetType().Name + " " + MoApi.FirstLine(ex.Message));
                call.Lost = ex;
            }
            return call;
        }
    }

    // U8 保存的结果：Result 是 BF 返回的 Hashtable（可能为 null），Lost 是调用中的异常（结果未知）。
    internal sealed class MoGenCall
    {
        public Hashtable Result;
        public Exception Lost;
    }
}
