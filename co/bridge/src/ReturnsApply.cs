using System;
using System.Collections.Generic;

namespace U8Co
{
    // K12 退货申请单（sale_return_apply）写入的分派与闸门。Dispatch.Handle 先问这里，返回 null 再走原来的路由表。
    // 新增 SaleGen.CreateApply（SaleGenApply.cs），修改、删除 ReturnsApplyEdit，审核 / 弃审在这里（VerifyVouch，VT 34）；
    // 退货单参照退货申请单生成（type=sale_return，source_type=sale_return_apply）走 SaleGen.ReturnFromApply。关闭不开放（400）。
    // VT 34 的保存、删除、审核由 U8 的 SaVoucherService 自行提交，桥不包 CoTrans（ReturnsApplyTran）。
    // 功能权限（U8 授权目录 UA_Auth）：录入 SA03250101（修改同录入）、审核 SA03250102、弃审 SA03250103、删除 SA03250110；
    // 退货单参照退货申请单 SA03020218。写路由每次现读权限（PermCheck.Require），任何 COM 调用之前。
    internal static class ReturnsApply
    {
        const string V = "/u8co/v1/vouchers/";
        const string StateSql = "select cCode as code, cVerifier as verifier, dverifydate as verify_date, cCloser as closer, "
            + "isnull(iswfcontrolled,0) as wf, isnull(iverifystate,0) as vstate from SA_ReturnsApplyMain where ID=?";
        // 下游：退货单行 irtnappid 指本单行，或本单行已有累计退货数量 fretqty。
        const string DownSql = "select top 1 convert(varchar(20), d.iDLsID) from DispatchLists d "
            + "inner join SA_ReturnsApplyDetail a on a.AutoID=d.irtnappid where a.ID=?";
        const string UsedSql = "select top 1 convert(varchar(20), AutoID) from SA_ReturnsApplyDetail "
            + "where ID=? and isnull(fretqty,0)<>0";
        const string LinesSql = "select count(*) from SA_ReturnsApplyDetail where ID=?";

        public static ApiResult Try(WorkContext ctx, string path)
        {
            if (ctx == null || ctx.Item == null)
            {
                return null;
            }
            WorkItem item = ctx.Item;
            if (path == V + "generate" && SaleReturn.Is(item.Type) && ReturnsApplyRead.Is(item.Source))
            {
                PermCheck.Require(ctx, "SA03020218", "退货单参照退货申请单");
                ApiResult made = SaleGen.ReturnFromApply(ctx, item.Type, item.Id,
                    item.Head ?? new Dictionary<string, object>(), item.Lines ?? new object[0]);
                return PuAppRoutes.StampNewId(ctx, made);
            }
            if (!ReturnsApplyRead.Is(item.Type))
            {
                return null;
            }
            return Route(ctx, path, item);
        }

        static ApiResult Route(WorkContext ctx, string path, WorkItem item)
        {
            if (path == V + "create")
            {
                PermCheck.Require(ctx, "SA03250101", "退货申请单录入");
                return PuAppRoutes.StampNewId(ctx, SaleGen.CreateApply(ctx, item.Type, item.Head, item.Lines));
            }
            if (path == V + "update")
            {
                PermCheck.Require(ctx, "SA03250101", "退货申请单修改");
                return ReturnsApplyEdit.Update(ctx, item.Type, item.Id,
                    item.Head ?? new Dictionary<string, object>(), item.Lines ?? new object[0]);
            }
            if (path == V + "delete")
            {
                PermCheck.Require(ctx, "SA03250110", "退货申请单删除");
                return ReturnsApplyEdit.Delete(ctx, item.Type, item.Id);
            }
            if (path == V + "verify")
            {
                return Verify(ctx, item.Type, item.Id, item.Action);
            }
            return null;
        }

        // 表头状态（code、verifier、verify_date、closer、wf、vstate）；不存在 404。
        internal static Dictionary<string, object> State(object conn, int id)
        {
            Dictionary<string, object> snap = Rows.One(conn, StateSql, new object[] { id });
            if (snap == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            return snap;
        }

        // 修改、删除：未审核、未关闭、不在审批流里、没有下游退货单。
        internal static Dictionary<string, object> RequireEditable(object conn, int id)
        {
            Dictionary<string, object> snap = State(conn, id);
            RefuseWorkflow(snap);
            if (Verified(snap))
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            if (CoRows.Col(snap, "closer").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            RefuseDownstream(conn, id);
            return snap;
        }

        static void RefuseWorkflow(Dictionary<string, object> snap)
        {
            if (CoRows.FlagOf(snap, "wf") || PuInv.Num(CoRows.Col(snap, "vstate")) > 0m)
            {
                throw new BridgeException(409, "workflow_enabled", "退货申请单受审批流控制，请到 U8 客户端处理");
            }
        }

        static void RefuseDownstream(object conn, int id)
        {
            if (Rows.Scalar(conn, DownSql, new object[] { id }) != null || Rows.Scalar(conn, UsedSql, new object[] { id }) != null)
            {
                throw new BridgeException(409, "state_mismatch", "退货申请单已有退货单，请先删除退货单");
            }
        }

        static bool Verified(Dictionary<string, object> snap)
        {
            return CoRows.Col(snap, "verifier").Length > 0;
        }

        static ApiResult Verify(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            if (action != "verify" && action != "unverify")
            {
                throw new BridgeException(400, "bad_request", "action 必须是 verify 或 unverify");
            }
            bool on = action == "verify";
            PermCheck.Require(ctx, on ? "SA03250102" : "SA03250103", on ? "退货申请单审核" : "退货申请单弃审");
            Dictionary<string, object> snap = State(ctx.Conn, id);
            RefuseWorkflow(snap);
            if (on == Verified(snap))
            {
                throw new BridgeException(409, "state_mismatch", on ? "单据已审核" : "单据未审核");
            }
            if (!on && CoRows.Col(snap, "closer").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (!on)
            {
                RefuseDownstream(ctx.Conn, id);
            }
            RunVerify(ctx, id, on);
            return Verified(ctx, kind, id, action);
        }

        static void RunVerify(WorkContext ctx, int id, bool on)
        {
            object sys = null;
            object co = null;
            object[] doms = new object[2];
            try
            {
                SaSession.OpenSa(ctx.Conn, ctx.Session.Login, ReturnsApplyRead.SaVt, out sys, out co);
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                string err = SaSession.ReadSa(co, doms, id, ctx.Item);
                if (err.Length > 0)
                {
                    throw new BridgeException(409, "u8_rejected", err);
                }
                CoRows.RequireHead(doms[0], "ID", id);
                // VT 34 的 VerifyVouch 由 SaVoucherService 自行提交，不包 CoTrans（ReturnsApplyTran）。
                ReturnsApplyTran.Verify(ctx, co, doms[0], on, (on ? "审核" : "弃审") + "退货申请单 " + Id(id));
            }
            finally
            {
                SaSession.Release(sys, co, doms);
            }
        }

        // 提交后在新连接上回读：审核后审核人是登录操作员，弃审后审核人为空。U8 已自行提交，不符与回读失败都 504。
        static ApiResult Verified(WorkContext ctx, VoucherKind kind, int id, string action)
        {
            Dictionary<string, object> after = Reread(ctx, id, "");
            string verifier = CoRows.Col(after, "verifier");
            string name = ctx.OperatorName == null ? "" : ctx.OperatorName.Trim();
            if (action == "verify" ? verifier.Length == 0 || verifier != name : verifier.Length > 0)
            {
                CoRows.Note(ctx.Item, (action == "verify" ? "审核" : "弃审") + "后审核人「" + verifier + "」，登录操作员「" + name + "」");
                throw new BridgeException(504, "outcome_unknown", (action == "verify" ? "U8 已提交审核，回读审核人与登录操作员不一致"
                    : "U8 已提交弃审，回读审核人仍在") + "，需要人工核对：退货申请单 ID " + Id(id));
            }
            Dictionary<string, object> state = CoRows.StateOf(after);
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["acc"] = ctx.Item.Acc ?? "";
            body["id"] = id;
            body["action"] = action;
            body["verified_by"] = state["verifier"];
            body["verified_at"] = state["verified_at"];
            return ApiResult.Ok(body);
        }

        // 新增、修改之后的回读（EditMsg.Saved 的同构，CoRows.HeadRow 不认这张表）：ok、type、id、code、state、lines。
        internal static ApiResult Saved(WorkContext ctx, VoucherKind kind, int id, string code)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                Dictionary<string, object> row = Rows.One(conn, StateSql, new object[] { id });
                if (row == null)
                {
                    throw new BridgeException(404, "not_found", "单据不存在");
                }
                Dictionary<string, object> body = new Dictionary<string, object>();
                body["ok"] = true;
                body["type"] = kind.Name;
                body["id"] = id;
                body["code"] = CoRows.Col(row, "code");
                body["state"] = CoRows.StateOf(row);
                body["lines"] = (int)PuInv.Num(Rows.Scalar(conn, LinesSql, new object[] { id }));
                return ApiResult.Ok(body);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "Saved " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Unknown(id, code));
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        // 删除提交后在新连接上确认表头已不在；还在 409，回读失败 504。
        internal static ApiResult Gone(WorkContext ctx, VoucherKind kind, int id)
        {
            Dictionary<string, object> row = Reread(ctx, id, "");
            if (row != null)
            {
                throw new BridgeException(409, "state_mismatch", "U8 返回成功但回读状态不符");
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["deleted"] = true;
            return ApiResult.Ok(body);
        }

        static Dictionary<string, object> Reread(WorkContext ctx, int id, string code)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                return Rows.One(conn, StateSql, new object[] { id });
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "Reread " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Unknown(id, code));
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static string Id(int id)
        {
            return id.ToString(System.Globalization.CultureInfo.InvariantCulture);
        }

        static string Unknown(int id, string code)
        {
            string text = "U8 已提交，回读退货申请单失败，需要人工核对";
            if (id > 0)
            {
                text += "：ID " + id.ToString(System.Globalization.CultureInfo.InvariantCulture);
            }
            if (code != null && code.Trim().Length > 0)
            {
                text += "，单号 " + code.Trim();
            }
            return text;
        }
    }
}
