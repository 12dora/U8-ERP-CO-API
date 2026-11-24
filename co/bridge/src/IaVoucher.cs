using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 存货核算制单（ia/voucher，第二级写入，只对测试账套开放）：把已记账、未制单的出入库单据生成总账凭证
    // （U8「存货核算 → 财务核算 → 生成凭证」）。U8 的制单是界面 frmMakePz，没有无界面组件：桥照它的规则拼分录
    // （IaVoucherAcct 科目、IaVoucherLines 合并与排列、IaVoucherPlan 计划），经凭证导入 U8PzInsert.Transact 保存（它自己提交），
    // 再按 U8 制单后的样子补凭证来源列、回写存货明细账（IaVoucherBack）。步骤：
    // (1) 事务：带锁读存货明细账、过闸门（含总账里已有本单却没有回写的凭证 → 409）、拼全部凭证、逐张从 Ap_CancelNo 取外部业务号，提交；
    // (2) 逐张：Transact 保存（IA 被拒时改 GL 再导一次）→ 核对凭证号上的凭证 → 事务里补来源列、回写、核对（行数、撞号），提交；
    //     回写失败就在新事务里删掉刚生成的凭证并 409；删也失败或结果不明时 504。第 2 张起失败时前面的保留，504 列出已完成的；
    // (3) 全部完成后在新连接上回读。预演（validate）在 (1) 的提交处结束，detail.vouchers 是要生成的凭证和分录。
    internal static class IaVoucher
    {
        public static ApiResult Run(WorkContext ctx)
        {
            if (ctx == null || ctx.Session == null)
            {
                throw new BridgeException(500, "internal", "缺少 U8 登录");
            }
            TestAccountGate.Require(ctx.Item, IaVoucherReq.TestOnly);
            IaJob job = new IaJob();
            job.Ask = IaVoucherReq.Parse(ctx.Item.Body);
            PermCheck.RequireRule(PermCheck.Of(ctx), PermRegistry.ForKey(IaVoucherReq.RuleKey(false)));
            if (!PeriodModules.StartedSubs(ctx).Contains(IaReq.Sub))
            {
                throw IaVoucherLoad.Refuse("存货核算未启用");
            }
            job.Year = GlState.LoginYear(ctx);
            job.Date = job.Ask.Date.Length > 0 ? job.Ask.Date : GlState.LoginDate(ctx);
            CoRows.Note(ctx.Item, IaVoucherReq.Title(job.Ask));
            Prepare(ctx, job);
            string maker = GlState.Operator(ctx);
            foreach (IaPlan plan in job.Plans)
            {
                IaVoucherSave.Save(ctx, job, plan, maker);
                IaVoucherSave.Identify(ctx, job, plan);
                Finish(ctx, job, plan);
                job.Done++;
            }
            return Readback(ctx, job);
        }

        static void Prepare(WorkContext ctx, IaJob job)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                IaVoucherLoad.Load(conn, job);
                IaVoucherPlan.Make(conn, job);
                foreach (IaPlan plan in job.Plans)
                {
                    plan.PzId = IaVoucherNo.Allocate(conn, job.Year);
                }
                IaVoucherView.Dry(job);
                ctx.Item.TranAfter = CoTrans.Count(conn);
                CoTrans.CommitSeen(conn);
                open = false;
            }
            catch (Exception)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                throw;
            }
        }

        static void Finish(WorkContext ctx, IaJob job, IaPlan plan)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                if (CoTrans.Count(conn) != "0")
                {
                    throw IaVoucherSave.Saved(job, plan, "导入后连接上仍有未结束的事务，未回写存货明细账");
                }
                CoTrans.Begin(conn);
                open = true;
                IaVoucherBack.PatchGl(conn, plan, ctx.Item.Acc);
                IaVoucherBack.Mark(conn, plan);
                // 撞号总是先查：撞了号补偿就不能按外部业务号清回写（会清到别人的）。
                string problem = IaVoucherBack.Verify(conn, plan);
                if (problem != null)
                {
                    throw IaVoucherLoad.Refuse(problem);
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
                CoRows.Note(ctx.Item, "存货核算制单回写 " + ex.Message);
                throw IaVoucherSave.Partial(job, Compensate(ctx, job, plan, known != null ? known.Message : "回写存货明细账出错"));
            }
        }

        // 回写失败：在新事务里删掉刚生成的凭证（未记账、未审核、未签字才删）并清掉可能的回写，返回 409；删不掉 504。
        // 外部业务号撞号时回写事务已回滚，别的凭证 / 单据也用着这个号：只按凭证键删本凭证、核对它不在了，不按外部业务号清。
        static BridgeException Compensate(WorkContext ctx, IaJob job, IaPlan plan, string why)
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
                    IaVoucherBack.Clear(conn, plan.PzId);
                }
                if (GlState.Read(conn, plan.Key, true) != null || (!plan.Clash && IaVoucherBack.Left(conn, plan.PzId) != 0))
                {
                    throw new InvalidOperationException("删除后仍有残留");
                }
                CoTrans.CommitSeen(conn);
                open = false;
            }
            catch (Exception ex)
            {
                CoRows.CatchTran(conn, ctx.Item, open);
                CoRows.Note(ctx.Item, "存货核算制单补偿 " + ex.Message);
                return IaVoucherSave.Saved(job, plan, "回写存货明细账失败（" + why + "），删除刚生成的凭证也失败；请在 U8 里删除该凭证后再制单");
            }
            return new BridgeException(409, "state_mismatch", "制单回写失败（" + why + "），已删除刚生成的凭证 " + plan.Key.Text()
                + "（外部业务号 " + plan.PzId + "），单据仍未制单");
        }

        // 已提交。新连接（不加 NOLOCK）逐张回读凭证行和存货明细账；读不出来或对不上 504（已提交，不要重投）。
        static ApiResult Readback(WorkContext ctx, IaJob job)
        {
            object fresh = null;
            IaPlan at = job.Plans[0];
            try
            {
                fresh = ctx.OpenFresh();
                List<object> vouchers = new List<object>();
                foreach (IaPlan plan in job.Plans)
                {
                    at = plan;
                    List<Dictionary<string, object>> rows = Rows.Query(fresh, ArapVoucherView.LineSql, new object[] { plan.Key.Year, plan.PzId },
                        IaVoucherLines.LineMax + 1);
                    string marked = Rows.Scalar(fresh, "select convert(varchar(12), count(*)) from IA_Subsidiary where cPZID=?",
                        new object[] { plan.PzId });
                    if (rows.Count != plan.Draft.Lines.Count || CoRows.AsId(marked) != plan.RowCount())
                    {
                        throw IaVoucherSave.Saved(job, plan, "回读凭证行或存货明细账不符");
                    }
                    vouchers.Add(IaVoucherView.Voucher(plan, rows));
                }
                return ApiResult.Ok(IaVoucherView.Body(ctx, job, vouchers));
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "存货核算制单回读 " + ex.Message);
                throw IaVoucherSave.Saved(job, at, "回读失败");
            }
            finally
            {
                AdoXml.Close(fresh);
            }
        }
    }

    // 响应：{ok, acc, merge, voucher_date, sign, count, vouchers:[{pz_id, making_system, year, period, sign, no, num, date,
    // bills:[{type, id, code, rows}], lines:[…]}], skipped:[{type, id, code, reason}], pending:[{type, id}]}（pending：按月份逐张制单
    // 超过一次上限、留到下次的单据）。预演的 detail.vouchers 同形（没有 no、num、lines 的 entry）。
    internal static class IaVoucherView
    {
        public static Dictionary<string, object> Body(WorkContext ctx, IaJob job, List<object> vouchers)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx.Item.Acc;
            body["merge"] = job.Ask.All ? IaVoucherReq.AllBills : IaVoucherReq.PerBill;
            body["voucher_date"] = job.Date;
            body["sign"] = job.Sign;
            body["count"] = vouchers.Count;
            body["vouchers"] = vouchers;
            body["skipped"] = job.Skipped();
            body["pending"] = job.Pending();
            return body;
        }

        public static Dictionary<string, object> Voucher(IaPlan plan, List<Dictionary<string, object>> rows)
        {
            Dictionary<string, object> one = Head(plan);
            one["no"] = plan.Key.No;
            one["num"] = plan.PzNum();
            one["making_system"] = plan.MakingSystem;
            List<object> lines = new List<object>();
            foreach (Dictionary<string, object> row in rows)
            {
                Dictionary<string, object> line = new Dictionary<string, object>();
                line["entry"] = CoRows.AsId(CoRows.Col(row, "inid"));
                line["account"] = CoRows.Col(row, "ccode");
                line["digest"] = CoRows.Col(row, "cdigest");
                line["debit"] = WriteoffSql.Num(CoRows.Col(row, "md"));
                line["credit"] = WriteoffSql.Num(CoRows.Col(row, "mc"));
                string[][] aux = new string[][]
                {
                    new string[] { "dept", "cdept_id" }, new string[] { "person", "cperson_id" }, new string[] { "customer", "ccus_id" },
                    new string[] { "supplier", "csup_id" }, new string[] { "item_class", "citem_class" }, new string[] { "item", "citem_id" },
                    new string[] { "bill_code", "coutid" }
                };
                foreach (string[] pair in aux)
                {
                    string value = CoRows.Col(row, pair[1]);
                    line[pair[0]] = value.Length > 0 ? value : null;
                }
                lines.Add(line);
            }
            one["lines"] = lines;
            return one;
        }

        // 已完成的一张凭证的简要（部分完成时的 detail.done）。
        public static Dictionary<string, object> Brief(IaPlan plan)
        {
            Dictionary<string, object> one = Head(plan);
            one["no"] = plan.Key.No;
            return one;
        }

        static Dictionary<string, object> Head(IaPlan plan)
        {
            Dictionary<string, object> one = new Dictionary<string, object>();
            one["pz_id"] = plan.PzId;
            one["year"] = plan.Key.Year;
            one["period"] = plan.Key.Period;
            one["sign"] = plan.Key.Sign;
            one["date"] = plan.Date;
            List<object> bills = new List<object>();
            foreach (IaBill bill in plan.Bills)
            {
                Dictionary<string, object> b = new Dictionary<string, object>();
                b["type"] = bill.Type.Code;
                b["id"] = bill.Id;
                b["code"] = bill.Code;
                b["rows"] = bill.Rows.Count;
                bills.Add(b);
            }
            one["bills"] = bills;
            return one;
        }

        // 预演：Prepare 的事务里拼好分录、取到外部业务号之后、提交之前交出计划；提交钩子随后回滚（取号一并撤销），不调用导入。
        public static void Dry(IaJob job)
        {
            if (!DryRun.Active)
            {
                return;
            }
            List<object> vouchers = new List<object>();
            foreach (IaPlan plan in job.Plans)
            {
                Dictionary<string, object> one = Head(plan);
                List<object> lines = GlDryRun.Lines(plan.Draft);
                for (int i = 0; i < lines.Count && i < plan.Src.Count; i++)
                {
                    Dictionary<string, object> line = lines[i] as Dictionary<string, object>;
                    if (line != null)
                    {
                        line["bill_type"] = plan.Src[i].BillType;
                        line["bill_code"] = plan.Src[i].BillCode;
                    }
                }
                one["lines"] = lines;
                one["lines_total"] = plan.Draft.Lines.Count;
                vouchers.Add(one);
            }
            DryRun.Set("vouchers", vouchers);
            DryRun.Set("pz_id_note", "预演取的外部业务号已随回滚撤销，实际制单会重新取号");
            DryRun.Set("sign", job.Sign);
            DryRun.Set("voucher_date", job.Date);
            DryRun.Set("skipped", job.Skipped());
            DryRun.Set("pending", job.Deferred());
        }
    }
}
