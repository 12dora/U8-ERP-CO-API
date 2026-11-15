using System;
using System.Collections.Generic;

namespace U8Co
{
    // 记账 gl/vouchers/post：在写工人线程上直接调用 U8 自己的总账 .NET 组件（GlPostNet），不经 FAG HTTP 池、不经 COM。
    // 先在请求连接上查清楚（GlPostCheck），年度首张凭证另做期初对账和试算（GlPostFirst），再在一个 TransactionScope 里
    // 汇总、记账、补偿 U8 不带年度回写记账标志的缺陷（GlPostTx、GlPostSnap），提交后在新连接上回读。不做取消记账。
    internal static class GlPost
    {
        const string PosterSql = "SELECT MIN(ISNULL(CONVERT(int, ibook),0)) b, MIN(ISNULL(cbook,N'')) c1, MAX(ISNULL(cbook,N'')) c2"
            + " FROM GL_accvouch" + GlState.KeyWhere;

        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "记账需要登录对象");
            }
            GlPostReq req = GlPostParse.Parse(ctx.Item.Body, GlState.LoginYear(ctx));
            CoRows.Note(ctx.Item, "记账 " + req.Summary());
            PermCheck.RequireRule(PermCheck.Of(ctx), PermRegistry.ForKey(GlPostParse.Rule));
            req.Poster = Poster(ctx);
            GlPostCheck.Run(ctx.Conn, req);
            GlDryRun.Post(ctx, req);
            string conn = GlPostSql.Text(AdoXml.ConnectionString(ctx.Session.Login, ctx.Config));
            GlPostNet.Enter();
            try
            {
                Post(ctx, req, conn);
            }
            finally
            {
                GlPostNet.Leave();
            }
            return Readback(ctx, req);
        }

        // U8 把 UserData.UserName 直接拼进 N'…'（AccVouchPost），姓名带单引号会让它的 SQL 出错，调用前拒绝。
        static string Poster(WorkContext ctx)
        {
            string name = GlState.Operator(ctx);
            if (name.IndexOf('\'') >= 0)
            {
                throw GlState.Refuse("操作员姓名含单引号，U8 的记账组件不能处理");
            }
            return name;
        }

        static void Post(WorkContext ctx, GlPostReq req, string conn)
        {
            GlPostNet net = GlPostNet.Load();
            net.Bind(net.NewUser(conn, req, ctx.Item, GlState.LoginDate(ctx)));
            try
            {
                GlPostCheck.FirstPosting(ctx.Conn, net, req.Year, conn, ctx.Item.Acc);
                GlPostTx.Run(net, conn, req);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "GlPost " + ex.GetType().Name + " " + GlPostTx.FirstLine(ex.Message));
                throw;
            }
            finally
            {
                net.Unbind();
            }
        }

        // 已提交。回读失败、或读到的不是本操作员记账的状态，都是 504 outcome_unknown（消息写明已提交）。
        static ApiResult Readback(WorkContext ctx, GlPostReq req)
        {
            object fresh = null;
            try
            {
                fresh = ctx.OpenFresh();
                List<object> posted = new List<object>();
                foreach (GlPostItem item in req.Items)
                {
                    posted.Add(Posted(fresh, req, item));
                }
                Dictionary<string, object> body = new Dictionary<string, object>();
                body["ok"] = true;
                body["period"] = req.Period;
                body["fiscal_year"] = req.Year;
                body["posted"] = posted;
                // 提交前已核对记账事务没有升级成 MSDTC 分布式事务（GlPostTx.Local），走到这里恒为 true。
                body["local_txn"] = true;
                return ApiResult.Ok(body);
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "GlPostReadback " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交，但回读凭证状态失败：" + req.Summary()
                    + "；请先查询再决定是否重试");
            }
            finally
            {
                AdoXml.Close(fresh);
            }
        }

        static Dictionary<string, object> Posted(object conn, GlPostReq req, GlPostItem item)
        {
            GlKey key = req.Key(item);
            GlHead head = GlState.Read(conn, key, false);
            Dictionary<string, object> row = Rows.One(conn, PosterSql, GlSql.KeyArgs(key));
            string first = GlSql.Col(row, "c1");
            bool mine = GlSql.Int(row, "b") == 1 && first == req.Poster && GlSql.Col(row, "c2") == req.Poster;
            if (head == null || !head.Posted || !mine)
            {
                throw new BridgeException(504, "outcome_unknown", "记账已提交，但回读凭证 " + key.Text() + " 不是本操作员记账的状态（" + req.Summary()
                    + "）；请先查询核对，不要直接重试");
            }
            Dictionary<string, object> state = GlState.StateOf(head);
            state["poster"] = first;
            Dictionary<string, object> one = new Dictionary<string, object>();
            one["sign"] = item.Sign;
            one["no"] = item.No;
            one["state"] = state;
            return one;
        }
    }
}
