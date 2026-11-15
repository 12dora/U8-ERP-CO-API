using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 生产订单修改（vouchers/update，type=production_order，id=MoId）：U8API MOrderLoad + MOrderUpdate，登录子系统 MO。
    // 改未关闭的订单（全部行 Status 1 / 2 / 3、非集合、不受审批流控制、没报检、没被产成品入库引用）。已审核的照 U8 客户端
    // 「变更」直接改、状态保持已审核；已领料的改后需求不能少于已领，材料出库单行的子件关联由 MoUpdateRemap 改写到新子件。
    // 调用前在请求连接上读快照（行、全部子件、材料出库引用）；调用后在新连接上回读，行字段要等于请求，每行子件数不变、
    // 子件数量等于原值或按新行数量重算的值，否则 504 outcome_unknown（MOrderUpdate 会重写全部子件，不能悄悄丢用料）。
    // API 自己开 TransactionScope 提交，不包 CoTrans。见 docs/u8-notes.md「生产订单修改」。
    internal static class MoUpdate
    {
        public const string UpdateRule = "write:production_order:update";

        public static ApiResult Run(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> head, object[] lines)
        {
            U8Resolve.Enter();
            try
            {
                return Core(ctx, kind, id, head, lines);
            }
            finally
            {
                U8Resolve.Leave();
            }
        }

        static ApiResult Core(WorkContext ctx, VoucherKind kind, int id, Dictionary<string, object> head, object[] lines)
        {
            if (kind == null || kind.Name != "production_order")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持修改");
            }
            MoUpdateAsk ask = MoUpdateReq.Parse(head, lines);
            PermContext perm = PermCheck.Of(ctx);
            PermRule rule = PermRegistry.ForKey(UpdateRule);
            PermCheck.RequireRule(perm, rule);
            MoSnap before = MoUpdateSql.Load(ctx.Conn, id);
            if (before.Lines.Count == 0)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            MoUpdateSql.Gate(ctx.Conn, id, before);
            MoDelete.CheckRows(perm, rule, RowsOf(before));
            MoUpdateSql.Digits(ctx.Conn, before);
            MoRefs refs = MoUpdateRemap.Capture(ctx.Conn, id, before);
            MoPlan plan = MoUpdatePlan.Build(ask, before);
            MoUpdateRemap.CheckIssued(plan);
            // 预演（校验模式）：MOrderUpdate 自己提交，计划算完就停（没有改动时也停，detail.input.changed=false）。
            MoDry.UpdateInput(plan);
            DryRun.Stop(ctx, plan.Changed ? "U8API MOrderUpdate" : "无改动，不调用 U8");
            if (!plan.Changed)
            {
                // 请求的值与现值相同：不调用 U8（MOrderUpdate 会重写全部子件，没必要冒险）。
                return MoUpdateCheck.Result(kind, id, before, plan);
            }
            MoUpdateRestore.LogSnapshot(ctx, id, before);
            MoUpdateRemap.LogRefs(ctx, id, before.Code, refs);
            Exception lost = MoUpdateCom.Invoke(ctx, before.Code, plan);
            MoSnap after = Reread(ctx, id, before);
            bool untouched = Untouched(before, after);
            if (lost != null && untouched)
            {
                throw NotApplied(lost);
            }
            if (!untouched)
            {
                after = Settle(ctx, id, plan, before, refs, after);
            }
            Confirm(ctx, plan, before, after, lost);
            return MoUpdateCheck.Result(kind, id, after, plan);
        }

        // U8 已提交并重插了子件：先把材料出库引用改到新子件（MoUpdateRemap），再把 U8 丢掉的关联列按快照写回
        // （MoUpdateRestore），然后完整回读。两步互不依赖：改写失败也照样写回（写回自带重插、未被改过的核对），
        // 两步的 504 合并成一条再抛。
        static MoSnap Settle(WorkContext ctx, int id, MoPlan plan, MoSnap before, MoRefs refs, MoSnap after)
        {
            BridgeException remap = null;
            try
            {
                MoUpdateRemap.Run(ctx, id, before, refs);
            }
            catch (BridgeException ex)
            {
                if (ex.Status != 504)
                {
                    throw;
                }
                remap = ex;
            }
            bool restore = MoUpdateCheck.Diff(plan, after).Count > 0;
            BridgeException restored = null;
            if (restore)
            {
                try
                {
                    MoUpdateRestore.Run(ctx, id, plan, before.Code);
                }
                catch (BridgeException ex)
                {
                    if (ex.Status != 504)
                    {
                        throw;
                    }
                    restored = ex;
                }
            }
            BridgeException failed = Combine(remap, restored);
            if (failed != null)
            {
                throw failed;
            }
            return restore ? Reread(ctx, id, before) : after;
        }

        // 改写（MoUpdateRemap）与写回（MoUpdateRestore）的 504 合并：只有一个就原样，两个都有就拼在一起。纯逻辑，--selftest 覆盖。
        internal static BridgeException Combine(BridgeException remap, BridgeException restore)
        {
            if (remap == null || restore == null)
            {
                return remap ?? restore;
            }
            return new BridgeException(504, "outcome_unknown", remap.Message + "。另外，子件关联列也没有写回：" + restore.Message);
        }

        static List<Dictionary<string, object>> RowsOf(MoSnap snap)
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            for (int i = 0; i < snap.Lines.Count; i++)
            {
                rows.Add(snap.Lines[i].Row);
            }
            return rows;
        }

        static MoSnap Reread(WorkContext ctx, int id, MoSnap before)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                MoSnap after = MoUpdateSql.Load(conn, id);
                after.QtyDigits = before.QtyDigits;
                after.AuxDigits = before.AuxDigits;
                return after;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "MoUpdate " + MoApi.FirstLine(ex.Message));
                throw new BridgeException(504, "outcome_unknown", "已提交修改但未能回读，生产订单 " + before.Code);
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        // 调用失败且回读与修改前完全相同（U8 没改）：IPC 错误 503，其余按 U8 拒绝 409 u8_rejected（原文第一行）。
        static BridgeException NotApplied(Exception lost)
        {
            if (MoApi.IsIpc(lost.Message))
            {
                return new BridgeException(503, "u8_unavailable", "U8 生产制造服务未运行");
            }
            return MoApi.Refused(lost.Message);
        }

        // 回读等于目标就算成功（即使调用有异常或 U8 报了错）；否则一律 504，消息带前几项不符，审计 detail 记全部。
        static void Confirm(WorkContext ctx, MoPlan plan, MoSnap before, MoSnap after, Exception lost)
        {
            List<string> bad = MoUpdateCheck.Diff(plan, after);
            if (bad.Count == 0)
            {
                return;
            }
            CoRows.Note(ctx.Item, "MoUpdate 回读不符：" + string.Join("；", bad.ToArray()));
            string head = lost == null ? "U8 返回成功但回读与请求不符" : "U8 修改调用报错但库里已有改动，结果未知";
            if (lost != null)
            {
                string text = MoApi.FirstLine(lost.Message);
                head += text.Length > 0 ? "（" + text + "）" : "";
            }
            int shown = Math.Min(3, bad.Count);
            throw new BridgeException(504, "outcome_unknown",
                head + "，生产订单 " + before.Code + "：" + string.Join("；", bad.GetRange(0, shown).ToArray())
                + (bad.Count > shown ? "等 " + bad.Count.ToString(CultureInfo.InvariantCulture) + " 项" : "") + "。请到 U8 客户端核对用料");
        }

        // 回读与修改前的快照完全相同（行字段、每行子件数、数量和 MoUpdateCols 名单里的列都没变）。
        static bool Untouched(MoSnap before, MoSnap after)
        {
            try
            {
                return MoUpdateCheck.Diff(MoUpdatePlan.Build(new MoUpdateAsk(), before), after).Count == 0;
            }
            catch (BridgeException)
            {
                return false;
            }
        }
    }
}
