using System;
using System.Collections.Generic;

namespace U8Co
{
    // 处理制单（arap/process/voucher）：应收冲应付 9I、应付冲应收 9J（coutsign ZZ）、并账 BZ（BZ）、汇兑损益 9M（SY）、
    // 票据结算 9A / 贴现 9D / 背书 9E / 退回 9C（PJ，ArapProcVoucherNotes）、坏账计提 9F / 发生 9G / 收回 9H（JT，ArapProcVoucherBad）的
    // 1 到 50 个批次合成一张转账凭证。U8 只有「制单处理」界面，没有无界面组件；桥照 U8 的分录规则拼（ArapProcVoucherParts / Lines），
    // 经凭证导入 U8PzInsert.Transact 保存（ArapVoucherSave，自己提交），再按 U8 的结果回写（ArapProcVoucherBack）。三步同 arap/voucher：
    // (1) 事务：带锁读批次明细、过闸门（含防重复制单 NoOrphan）、拼分录、从 Ap_CancelNo 取外部业务号（PZ / flag），提交；
    // (2) Transact 保存凭证；
    // (3) 事务：补凭证来源列、回写两张往来明细、核对（含外部业务号没撞号），提交。失败在新事务里删掉刚生成的凭证并 409；
    //     删不掉 504。提交后在新连接上回读。汇兑损益、坏账处理是第二级写入，只对测试账套开放。取消制单走 arap/voucher/delete。
    internal static class ArapProcVoucher
    {
        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            ProcVoucherAsk ask = ArapProcVoucherReq.Parse(ctx.Item.Body);
            ArapProcVoucherReq.TestGate(ctx.Item, ask);
            PermRule rule = PermRegistry.ForKey(PermRegistry.ArapVoucherKey(ask.Flag, false));
            PermCheck.RequireRule(PermCheck.Of(ctx), rule);
            ProcPlan plan = Prepare(ctx, ask, rule, GlState.LoginYear(ctx));
            ArapVoucherSave.Save(ctx, plan.Gl);
            ArapVoucherSave.Identify(ctx, plan.Gl);
            Finish(ctx, plan);
            return Readback(ctx, plan);
        }

        static ProcPlan Prepare(WorkContext ctx, ProcVoucherAsk ask, PermRule rule, int loginYear)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                ProcPlan plan = new ProcPlan();
                plan.Ask = ask;
                // 坏账处理：HZAR 按库识别成 9F / 9G / 9H；计提 9F 没有往来明细，批次行由坏账准备参数拼出，也没有往来单位可查权限。
                if (ask.Bad)
                {
                    ArapProcVoucherBad.Resolve(conn, ask);
                }
                plan.Rows = ask.Style == "9F" ? ArapProcVoucherBad.LoadPara(conn, ask) : ArapProcVoucherLoad.Load(conn, ask);
                ArapProcVoucherLoad.CheckRows(conn, ask, plan.Rows);
                if (ask.Style != "9F")
                {
                    ArapProcVoucherLoad.Allowed(ctx, rule, ask, plan.Rows);
                }
                ArapProcVoucherLoad.NoOrphan(conn, ask, plan.Rows);
                // 票据处理：带锁读 AP_Note_Sub、逐批核对、防重复制单（其余处理不做什么）。
                ArapProcVoucherNotes.Prepare(conn, plan);
                string date = ArapProcVoucherLoad.Date(conn, ask, plan.Rows, loginYear);
                if (ask.ExchangeGain)
                {
                    ArapProcVoucherLines.CheckPl(conn, loginYear, ask.PlCode);
                }
                ArapProcVoucherPlan.Make(conn, plan, date, loginYear);
                GlCheck.Order(conn, plan.Gl.Key, date, false);
                plan.Gl.PzId = ArapVoucherNo.Allocate(conn, ask.Flag);
                ArapProcVoucherPlan.Dry(plan);
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.CommitSeen(conn);
                open = false;
                return plan;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        static void Finish(WorkContext ctx, ProcPlan plan)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                if (CoTrans.Count(conn) != "0")
                {
                    throw ArapVoucherSave.Saved(plan.Gl, "导入后连接上仍有未结束的事务，未回写往来明细");
                }
                CoTrans.Begin(conn);
                open = true;
                ArapProcVoucherBack.PatchGl(conn, plan, ctx.Item.Acc);
                ArapProcVoucherBack.Mark(conn, plan);
                // 撞号总是查：撞了号补偿就不能按外部业务号清回写（会清到别人的）。
                string clash = ArapVoucherBack.Clash(conn, plan.Gl);
                plan.Gl.Clash = clash != null;
                string problem = ArapProcVoucherBack.Mismatch(conn, plan) ?? clash;
                if (problem != null)
                {
                    throw ArapVoucherDoc.Refuse(problem);
                }
                CoTrans.CommitSeen(conn);
                open = false;
            }
            catch (DryRunDone)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                BridgeException known = ex as BridgeException;
                if (known != null && known.Code == "outcome_unknown")
                {
                    throw;
                }
                CoRows.Note(ctx.Item, "处理制单回写 " + ex.Message);
                throw Compensate(ctx, plan, known != null ? known.Message : "回写往来明细出错");
            }
        }

        // 回写失败：新事务里删掉刚生成的凭证（未记账、未审核、未签字才删）并清掉可能的回写，409；删不掉 504。撞号时只按凭证键删。
        static BridgeException Compensate(WorkContext ctx, ProcPlan plan, string why)
        {
            object conn = ctx.Conn;
            VoucherPlan gl = plan.Gl;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                GlHead head = GlState.Read(conn, gl.Key, true);
                if (head == null || head.Posted || head.Checker.Length > 0 || head.Cashier.Length > 0)
                {
                    throw new InvalidOperationException("凭证状态已变");
                }
                ArapVoucherBack.DeleteGl(conn, gl.Key, gl.Seq);
                if (!gl.Clash)
                {
                    ArapProcVoucherBack.Clear(conn, gl.PzId);
                }
                if (GlState.Read(conn, gl.Key, true) != null
                    || (!gl.Clash && ArapVoucherBack.Left(conn, gl.PzId) + ArapProcVoucherNotes.Left(conn, gl.PzId)
                        + ArapProcVoucherBad.Left(conn, gl.PzId) != 0))
                {
                    throw new InvalidOperationException("删除后仍有残留");
                }
                CoTrans.CommitSeen(conn);
                open = false;
            }
            catch (Exception ex)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                CoRows.Note(ctx.Item, "处理制单补偿 " + ex.Message);
                return ArapVoucherSave.Saved(gl, "回写往来明细失败（" + why + "），删除刚生成的凭证也失败；请在 U8 里删除该凭证后再制单");
            }
            return new BridgeException(409, "state_mismatch", "制单回写失败（" + why + "），已删除刚生成的凭证 " + gl.Key.Text()
                + "（外部业务号 " + gl.PzId + "），批次仍未制单");
        }

        // 已提交。新连接（不加 NOLOCK）回读凭证行和往来明细；读不出来或对不上 504（已提交，不要重投）。
        static ApiResult Readback(WorkContext ctx, ProcPlan plan)
        {
            VoucherPlan gl = plan.Gl;
            object fresh = null;
            try
            {
                fresh = ctx.OpenFresh();
                List<Dictionary<string, object>> rows = Rows.Query(fresh, ArapVoucherView.LineSql, new object[] { gl.Key.Year, gl.PzId }, 201);
                int marked = ArapProcVoucherBack.Count(fresh, "select convert(varchar(12), (select count(*) from Ar_Detail where cPZid=?) "
                    + "+ (select count(*) from Ap_Detail where cPZid=?))", new object[] { gl.PzId, gl.PzId })
                    + ArapProcVoucherBad.ParaMarked(fresh, plan, gl.PzId);
                // 坏账收回 9H 另回写收款单审核行（ArapProcVoucherBad.ReceiptRows）。
                if (rows.Count != gl.Draft.Lines.Count || marked != plan.Rows.Count + ArapProcVoucherBad.ReceiptRows(plan))
                {
                    throw ArapVoucherSave.Saved(gl, "回读凭证行或往来明细不符");
                }
                return ApiResult.Ok(ArapProcVoucherPlan.Body(ctx, plan, rows));
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "处理制单回读 " + ex.Message);
                throw ArapVoucherSave.Saved(gl, "回读失败");
            }
            finally
            {
                AdoXml.Close(fresh);
            }
        }
    }
}
