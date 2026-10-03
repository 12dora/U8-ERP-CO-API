using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 取消汇兑损益（arap/exchange_gain/cancel）：按处理号（1 到 200 个），或按登记日期（登录日期 date）取消当天全部未制单的 9M。
    // 只对测试账套开放（第二级）。U8 没有可调用的取消组件：桥按 U8 取消汇兑损益时执行的 SQL 写（sql/arap/exgain_cancel*.sql，实测核对），在请求连接的一个事务里：
    // 带锁快照 → 闸门（处理号存在、未制单、期间未结账、之后没有别的汇兑损益或核销等处理）→ 恢复应收应付单 / 收付款单本币余额 →
    // 销售发票 UpdateBillForAR（Ussaupdispatch.clsWrite2Bill）、采购发票 UpdateBillForAP（Pu_Productinf.cls_ForAPsrv），同一连接（ArapExGainBill）
    // → 清核销人 → 删 9M 行；提交后新连接确认已删光。
    // 含进出口 RZ 等桥不做的类型的处理号（U8 做的）：按日期取消时跳过（个数进 skipped_batches），按处理号取消时 409。
    internal static class ArapExGainCancel
    {
        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            ExGainAsk ask = ArapExGainReq.Parse(ctx.Item.Path, ctx.Item.Body);
            // 入队后再查一次（汇兑损益是第二级，登录前已查过）。
            ArapExGainReq.TestGate(ctx.Item);
            PermContext p = PermCheck.Of(ctx);
            PermCheck.RequireRule(p, PermRegistry.ForKey(PermRegistry.ExGainKey(ask.Flag, true)));
            ExGainResult result = Tran(ctx, ask, p);
            Reread(ctx, ask, result);
            return ApiResult.Ok(Body(ctx, ask, result));
        }

        static ExGainResult Tran(WorkContext ctx, ExGainAsk ask, PermContext p)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                ExGainResult result = null;
                ArapWriteoffCheck.Guard(conn, delegate { result = Write(ctx, ask, p); });
                ArapExGainSql.Drop(conn);
                if (DryRun.Active)
                {
                    DryRun.Set(ArapExGainReq.CancelAction, Body(ctx, ask, result));
                }
                ArapExGain.Finish(ctx);
                open = false;
                CoRows.Note(ctx.Item, "已取消汇兑损益 " + result.Nos.Count.ToString(CultureInfo.InvariantCulture) + " 个处理号");
                return result;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        static ExGainResult Write(WorkContext ctx, ExGainAsk ask, PermContext p)
        {
            object conn = ctx.Conn;
            ExGainArgs args = new ExGainArgs();
            args.Flag = ask.Flag;
            args.RegDate = ask.Date;
            args.ByDate = ask.CancelNos.Count == 0;
            ArapExGainSql.Prepare(conn, args, new List<string>(), ask.CancelNos);
            ScriptResult first = ArapExGain.Script(ctx, ArapExGainSql.CancelScript, "com_ap");
            ExGainResult result = new ExGainResult();
            ArapExGain.Shape(result, first);
            Allowed(p, ask.Flag, result);
            result.Rows = ArapExGain.Count(first, "rows");
            result.SkippedBatches = ArapExGain.Count(first, "skipped");
            if (result.SkippedBatches > 0)
            {
                CoRows.Note(ctx.Item, "按日期取消跳过 U8 做的处理号 " + result.SkippedBatches.ToString(CultureInfo.InvariantCulture) + " 个");
            }
            if (ArapExGain.Count(first, "com_ar") > 0)
            {
                ArapExGainBill.Sale(ctx);
            }
            if (ArapExGain.Count(first, "com_ap") > 0)
            {
                ArapExGainBill.Purchase(ctx);
            }
            ScriptResult second = ArapExGain.Script(ctx, ArapExGainSql.CancelEndScript, "deleted");
            if (ArapExGain.Count(second, "deleted") != result.Rows || result.Rows == 0)
            {
                throw new BridgeException(409, "u8_rejected", "取消汇兑损益删除的明细行数不符，已回滚");
            }
            return result;
        }

        // 客户 / 供应商受数据权限控制时，每个处理号的往来单位都要有权限（处理号查库后才知道，在事务里判断，不符回滚）。
        static void Allowed(PermContext p, string flag, ExGainResult result)
        {
            string obj = flag == "AP" ? PermObj.Vendor : PermObj.Customer;
            if (!p.Controls(obj))
            {
                return;
            }
            foreach (Dictionary<string, object> one in result.Batches)
            {
                string code = one["partner"] as string;
                if (!p.Allow(obj, code))
                {
                    throw new BridgeException(403, "no_permission", "没有往来单位 " + code + " 的数据权限（处理号 " + one["cancel_no"] + "）");
                }
            }
        }

        // 已提交。新连接（不加 NOLOCK）确认这些处理号的 9M 行已删光；读不出来或还在都是 504 outcome_unknown。
        static void Reread(WorkContext ctx, ExGainAsk ask, ExGainResult result)
        {
            object conn = null;
            int left;
            try
            {
                conn = ctx.OpenFresh();
                left = ArapExGainSql.CountRows(conn, ask.Flag, result.Nos);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "ArapExGainCancel " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "取消汇兑损益已提交但未能回读");
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (left != 0)
            {
                throw new BridgeException(504, "outcome_unknown", "取消汇兑损益已提交，但回读时仍有 "
                    + left.ToString(CultureInfo.InvariantCulture) + " 行汇兑损益明细；请先在 U8 里核对，不要直接重试");
            }
        }

        static Dictionary<string, object> Body(WorkContext ctx, ExGainAsk ask, ExGainResult result)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx.Item.Acc;
            body["flag"] = ask.Flag;
            body["by_date"] = ask.CancelNos.Count == 0 ? (object)ask.Date : null;
            body["rows"] = result.Rows;
            body["batches"] = result.Batches;
            body["total"] = result.Total;
            body["skipped_batches"] = result.SkippedBatches;
            return body;
        }
    }
}
