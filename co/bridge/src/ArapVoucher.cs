using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 应收 / 应付制单（arap/voucher）：一张已审核的单据生成一张总账凭证；ids 给同类型的 2 到 20 张单据合并成一张凭证
    // （U8 的「合并制单」，凭证行 coutid 各写各的单号）。
    // U8 没有无界面的制单组件（只有「制单处理」界面），桥照 U8 的规则拼分录（ArapVoucherBuild / Lines / Plan），
    // 经 U8 的凭证导入 U8PzInsert.Transact 保存（它自己提交），再按 U8 制单后的回写结果写回（ArapVoucherBack，实测核对）。三步：
    // (1) 事务：逐张带锁读单据、过闸门（含总账里已有本单却没有往来明细引用的凭证 → 409，防 504 后重投出第二张）、拼分录、
    //     从 Ap_CancelNo 取外部业务号，提交；
    // (2) Transact 保存凭证（AR|AP 被拒时改 GL 再导一次，ArapVoucherSave）；
    // (3) 事务：补凭证来源列、回写往来明细 / 发票 / 表头、核对（含外部业务号没撞号），提交。(3) 失败就在新事务里删掉刚生成的凭证并 409；
    //     删也失败或结果不明时 504，消息带凭证号和外部业务号。提交后在新连接上回读。
    internal static class ArapVoucher
    {
        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            VoucherAsk ask = ArapVoucherReq.Parse(ctx.Item.Body);
            PermRule rule = PermRegistry.ForKey(PermRegistry.ArapVoucherKey(ask.Flag, false));
            PermCheck.RequireRule(PermCheck.Of(ctx), rule);
            VoucherPlan plan = Prepare(ctx, ask, rule, GlState.LoginYear(ctx));
            ArapVoucherSave.Save(ctx, plan);
            ArapVoucherSave.Identify(ctx, plan);
            Finish(ctx, plan);
            return Readback(ctx, plan);
        }

        static VoucherPlan Prepare(WorkContext ctx, VoucherAsk ask, PermRule rule, int loginYear)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                List<VoucherDoc> docs = Docs(ctx, ask, rule);
                string date = ask.Date.Length > 0 ? ask.Date : ArapVoucherDoc.LastDate(docs);
                foreach (VoucherDoc doc in docs)
                {
                    ArapVoucherDoc.CheckDate(conn, doc, date, loginYear);
                }
                VoucherPlan plan = ArapVoucherPlan.Make(conn, docs, date, loginYear);
                GlCheck.Order(conn, plan.Key, date, false);
                plan.PzId = ArapVoucherNo.Allocate(conn, ask.Flag);
                ArapVoucherDry.Plan(plan);
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

        // 逐张带锁读单据、过闸门（含防重复制单 NoOrphan）、查数据权限。合并制单时拒绝原因前加「第 i 张（id）」。
        static List<VoucherDoc> Docs(WorkContext ctx, VoucherAsk ask, PermRule rule)
        {
            List<VoucherDoc> docs = new List<VoucherDoc>();
            for (int i = 0; i < ask.Ids.Count; i++)
            {
                try
                {
                    VoucherDoc doc = ArapVoucherDoc.Load(ctx.Conn, ask, ask.Ids[i]);
                    ArapVoucherDoc.NoOrphan(ctx.Conn, doc);
                    if (!PermCheck.RowAllowed(PermCheck.Of(ctx), rule, doc.Head))
                    {
                        throw new BridgeException(403, "no_permission", PermCheck.DeniedData);
                    }
                    docs.Add(doc);
                }
                catch (BridgeException ex)
                {
                    if (!ask.Merge)
                    {
                        throw;
                    }
                    throw new BridgeException(ex.Status, ex.Code, "ids 第 " + ArapVoucherView.Int(i + 1) + " 张（id "
                        + ArapVoucherView.Int(ask.Ids[i]) + "）：" + ex.Message, FieldPath.Item("ids", i));
                }
            }
            return docs;
        }

        static void Finish(WorkContext ctx, VoucherPlan plan)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                if (CoTrans.Count(conn) != "0")
                {
                    throw ArapVoucherSave.Saved(plan, "导入后连接上仍有未结束的事务，未回写单据");
                }
                CoTrans.Begin(conn);
                open = true;
                ArapVoucherBack.PatchGl(conn, plan, ctx.Item.Acc);
                ArapVoucherBack.Mark(conn, plan);
                // 撞号总是查：撞了号补偿就不能按外部业务号清回写（会清到别人的）。
                string clash = ArapVoucherBack.Clash(conn, plan);
                plan.Clash = clash != null;
                string problem = ArapVoucherBack.Mismatch(conn, plan) ?? clash;
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
                CoRows.Note(ctx.Item, "制单回写 " + ex.Message);
                throw Compensate(ctx, plan, known != null ? known.Message : "回写单据出错");
            }
        }

        // 回写失败：在新事务里删掉刚生成的凭证（未记账、未审核、未签字才删）并清掉可能的回写，返回 409；删不掉 504。
        // 外部业务号撞号时回写事务已回滚，别的凭证 / 单据也用着这个号：只按凭证键删本凭证、核对它不在了，不按外部业务号清。
        static BridgeException Compensate(WorkContext ctx, VoucherPlan plan, string why)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                GlHead head = GlState.Read(conn, plan.Key, true);
                if (head == null || head.Posted || head.Checker.Length > 0 || head.Cashier.Length > 0)
                {
                    throw new InvalidOperationException("凭证状态已变");
                }
                ArapVoucherBack.DeleteGl(conn, plan.Key, plan.Seq);
                if (!plan.Clash)
                {
                    ArapVoucherBack.Clear(conn, plan.Doc.Flag, plan.PzId);
                }
                if (GlState.Read(conn, plan.Key, true) != null || (!plan.Clash && ArapVoucherBack.Left(conn, plan.PzId) != 0))
                {
                    throw new InvalidOperationException("删除后仍有残留");
                }
                CoTrans.CommitSeen(conn);
                open = false;
            }
            catch (Exception ex)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                CoRows.Note(ctx.Item, "制单补偿 " + ex.Message);
                return ArapVoucherSave.Saved(plan, "回写单据失败（" + why + "），删除刚生成的凭证也失败；请在 U8 里删除该凭证后再制单");
            }
            return new BridgeException(409, "state_mismatch", "制单回写失败（" + why + "），已删除刚生成的凭证 " + plan.Key.Text()
                + "（外部业务号 " + plan.PzId + "），单据仍未制单");
        }

        // 已提交。新连接（不加 NOLOCK）回读凭证行和往来明细；读不出来或对不上 504（已提交，不要重投）。
        static ApiResult Readback(WorkContext ctx, VoucherPlan plan)
        {
            object fresh = null;
            try
            {
                fresh = ctx.OpenFresh();
                List<Dictionary<string, object>> rows = Rows.Query(fresh, ArapVoucherView.LineSql, new object[] { plan.Key.Year, plan.PzId }, 201);
                string marked = Rows.Scalar(fresh, "select convert(varchar(12), count(*)) from " + plan.Doc.Detail
                    + " where cPZid=? and cProcStyle=cVouchType", new object[] { plan.PzId });
                if (rows.Count != plan.Draft.Lines.Count || CoRows.AsId(marked) != plan.RowCount())
                {
                    throw ArapVoucherSave.Saved(plan, "回读凭证行或往来明细不符");
                }
                return ApiResult.Ok(ArapVoucherView.Body(ctx, plan, rows));
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "制单回读 " + ex.Message);
                throw ArapVoucherSave.Saved(plan, "回读失败");
            }
            finally
            {
                AdoXml.Close(fresh);
            }
        }
    }

    // 响应：{ok, acc, flag, type, id, code, bills:[{type, id, code, vouch_type}], pz_id, making_system,
    // voucher:{year, period, sign, no, num, date}, lines:[...]}。type / id / code 是第一张单据（合并制单时全部单据见 bills）。
    internal static class ArapVoucherView
    {
        internal const string LineSql = "select convert(varchar(6), inid) as inid, ccode, isnull(cdigest,N'') as cdigest, "
            + "convert(varchar(40), convert(decimal(28,2), isnull(md,0))) as md, convert(varchar(40), convert(decimal(28,2), isnull(mc,0))) as mc, "
            + "cdept_id, cperson_id, ccus_id, csup_id, citem_class, citem_id, csettle, coutid from GL_accvouch where iyear=? and coutno_id=? "
            + "order by inid";

        public static Dictionary<string, object> Body(WorkContext ctx, VoucherPlan plan, List<Dictionary<string, object>> rows)
        {
            Dictionary<string, object> voucher = new Dictionary<string, object>();
            voucher["year"] = plan.Key.Year;
            voucher["period"] = plan.Key.Period;
            voucher["sign"] = plan.Key.Sign;
            voucher["no"] = plan.Key.No;
            voucher["num"] = plan.PzNum();
            voucher["date"] = plan.Date;
            List<object> lines = new List<object>();
            foreach (Dictionary<string, object> row in rows)
            {
                lines.Add(Line(row));
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx.Item.Acc;
            body["flag"] = plan.Doc.Flag;
            body["type"] = plan.Doc.Ask.Kind;
            body["id"] = plan.Doc.Id;
            body["code"] = plan.Doc.Code;
            body["bills"] = Bills(plan);
            body["pz_id"] = plan.PzId;
            body["making_system"] = plan.MakingSystem;
            body["voucher"] = voucher;
            body["lines"] = lines;
            return body;
        }

        // 本凭证覆盖的单据（按请求顺序）：type、id、code、vouch_type；单张制单也有一项。
        static List<object> Bills(VoucherPlan plan)
        {
            List<object> bills = new List<object>();
            foreach (VoucherDoc doc in plan.Docs)
            {
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["type"] = doc.Ask.Kind;
                one["id"] = doc.Id;
                one["code"] = doc.Code;
                one["vouch_type"] = doc.VType;
                bills.Add(one);
            }
            return bills;
        }

        static Dictionary<string, object> Line(Dictionary<string, object> row)
        {
            Dictionary<string, object> one = new Dictionary<string, object>();
            one["entry"] = CoRows.AsId(CoRows.Col(row, "inid"));
            one["account"] = CoRows.Col(row, "ccode");
            one["digest"] = CoRows.Col(row, "cdigest");
            one["debit"] = WriteoffSql.Num(CoRows.Col(row, "md"));
            one["credit"] = WriteoffSql.Num(CoRows.Col(row, "mc"));
            string[][] aux = new string[][]
            {
                new string[] { "dept", "cdept_id" }, new string[] { "person", "cperson_id" }, new string[] { "customer", "ccus_id" },
                new string[] { "supplier", "csup_id" }, new string[] { "item_class", "citem_class" }, new string[] { "item", "citem_id" },
                new string[] { "settle", "csettle" }, new string[] { "bill_code", "coutid" }
            };
            foreach (string[] pair in aux)
            {
                string value = CoRows.Col(row, pair[1]);
                one[pair[0]] = value.Length > 0 ? value : null;
            }
            return one;
        }

        internal static string Int(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }
}
