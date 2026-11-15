using System;
using System.Collections.Generic;

namespace U8Co
{
    internal sealed class GlReversePlan
    {
        public GlKey Source;
        public GlKey Red;
        public string Date;
        public string Maker;
        public int Seq;
        public int Attach;
        // 原凭证的外部业务号（coutno_id）；原来没有时桥照 U8 取号补上（BlueAssigned）。
        public string BlueNo;
        public bool BlueAssigned;
        // 红字凭证自己的外部业务号。
        public string RedNo;
        public GlDraft Draft;
        public GlCarry Carry;
        public List<Dictionary<string, object>> SourceRows;
        public List<Dictionary<string, object>> SourceCash;
    }

    // 红字冲销 gl/vouchers/reverse：把一张已记账的总账手工凭证原样复制成红字凭证（金额、原币、数量、现金流量取负），
    // 红字凭证的 cblueoutno_id = 原凭证的 coutno_id（U8 自己的冲销凭证就是这样关联的，已在测试账套核对）。
    // 没有无界面的 U8 冲销组件，保存走与 gl/vouchers/create 相同的凭证导入 U8PzInsert.Transact（它自己提交）。三步：
    // (1) 事务：带锁读原凭证、过闸门、拼红字分录，不写库（预演在这里取号预览，随回滚撤销）；
    // (2) Transact 保存红字凭证；U8 拒绝时 409，库里没有任何改动；
    // (3) 事务：照 U8 取外部业务号（Ap_Proc_CancelNo 的 PZ / GL 行）、给原凭证补 coutno_id，补红字凭证的 coutno_id、
    //     cblueoutno_id、附单据数和 GL_CashTable.csign，提交；在新连接上逐行核对是原凭证取负。
    // (2) 之后出错或核对不符一律 504 outcome_unknown（凭证可能已保存）。
    internal static class GlReverse
    {
        public static ApiResult Run(WorkContext ctx)
        {
            int loginYear = GlState.LoginYear(ctx);
            GlReverseAsk ask = GlReverseReq.Parse(ctx.Item.Body, loginYear);
            GlState.Permit(ctx, GlReverseReq.Op);
            GlReversePlan plan = Prepare(ctx, ask, loginYear);
            string xml = GlXml.Envelope(true, plan.Red, plan.Draft, new string[] { plan.Date, plan.Maker }, plan.Carry, null);
            // 预演已在 Prepare 的提交钩子处结束；这里只是兜底，自提交的导入组件之前不留写入。
            DryRun.Stop(ctx, "U8PzInsert.Transact");
            GlReply reply = GlSave.Transact(ctx, xml, plan.Red);
            if (reply.No <= 0 || (reply.Period > 0 && reply.Period != plan.Red.Period)
                || (reply.Year > 0 && reply.Year != plan.Red.Year))
            {
                CoRows.Note(ctx.Item, "GlReverse reply year=" + GlReverseReq.Int(reply.Year) + " period="
                    + GlReverseReq.Int(reply.Period) + " no=" + GlReverseReq.Int(reply.No));
                throw new BridgeException(504, "outcome_unknown", GlReverseBack.Unknown(plan, "U8 没有返回凭证号"));
            }
            plan.Red.No = reply.No;
            GlReverseBack.Finish(ctx, plan);
            return GlReverseBack.Readback(ctx, plan);
        }

        static GlReversePlan Prepare(WorkContext ctx, GlReverseAsk ask, int loginYear)
        {
            object conn = ctx.Conn;
            bool open = false;
            try
            {
                CoTrans.Begin(conn);
                open = true;
                ctx.Item.TranBefore = CoTrans.Count(conn);
                GlReversePlan plan = Plan(ctx, ask, loginYear);
                if (DryRun.Active)
                {
                    // 只为预演取号；实际冲销在保存之后的 Finish 事务里取，U8 拒绝时不留取号和补写。
                    Number(conn, plan);
                }
                GlReverseBack.Preview(plan);
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

        static GlReversePlan Plan(WorkContext ctx, GlReverseAsk ask, int loginYear)
        {
            object conn = ctx.Conn;
            GlReversePlan plan = new GlReversePlan();
            plan.Source = ask.Source();
            GlHead head = GlState.Need(conn, plan.Source, true);
            Gate(conn, head);
            plan.SourceRows = GlReverseSrc.ReadLines(conn, plan.Source);
            GlReverseSrc.Gate(plan.SourceRows);
            plan.SourceCash = GlReverseSrc.ReadCash(conn, plan.Source, GlState.SignSeq(conn, plan.Source.Sign));
            plan.Date = ask.Date.Length > 0 ? ask.Date : GlState.LoginDate(ctx);
            if (string.CompareOrdinal(plan.Date, head.Date) < 0)
            {
                throw GlReq.Bad("红字凭证日期 voucher_date 不能早于原凭证日期 " + head.Date, "voucher_date");
            }
            plan.Red = new GlKey();
            plan.Red.Year = loginYear;
            plan.Red.Sign = plan.Source.Sign;
            plan.Red.Period = GlSave.PeriodOf(plan.Date, loginYear);
            GlState.PeriodOpen(conn, plan.Red.Year, plan.Red.Period);
            plan.Seq = GlState.SignSeq(conn, plan.Red.Sign);
            plan.Draft = GlReverseSrc.Draft(plan.Source, head.Date, plan.SourceRows, plan.SourceCash);
            plan.Carry = GlReverseSrc.Carry(plan.SourceRows);
            plan.Attach = GlSql.Int(plan.SourceRows[0], "idoc");
            Validate(conn, plan);
            GlCheck.Order(conn, plan.Red, plan.Date, false);
            plan.Maker = GlState.Operator(ctx);
            plan.BlueNo = head.OutNo;
            return plan;
        }

        // U8 只冲销已记账的凭证（ibook=1）；作废的、外部系统生成的、已被冲销的不冲销。
        static void Gate(object conn, GlHead head)
        {
            if (head.Mixed)
            {
                throw GlState.Refuse("凭证各行状态不一致，请在 U8 客户端处理");
            }
            if (head.Flag == 1)
            {
                throw GlState.Refuse("凭证已作废，不能冲销");
            }
            if (!head.Posted)
            {
                throw GlState.Refuse("只能冲销已记账的凭证");
            }
            GlState.Manual(head);
            GlState.NotReversed(conn, head);
        }

        // 原凭证的科目、辅助核算到红字凭证的年度可能已变（封存、改了辅助核算）：照 create 的规则查，不合格 409。
        static void Validate(object conn, GlReversePlan plan)
        {
            try
            {
                GlCheck.Validate(conn, plan.Red.Year, plan.Draft);
            }
            catch (BridgeException ex)
            {
                if (ex.Status != 400)
                {
                    throw;
                }
                throw GlState.Refuse("原凭证的分录不能原样复制为红字凭证：" + ex.Message);
            }
        }

        // 外部业务号照 U8 填制凭证取号（VoucherRule：Ap_Proc_CancelNo 'PZ', 'GL'）。原凭证没有外部业务号（例如经凭证导入
        // 生成的）时先给它补一个，红字凭证的 cblueoutno_id 要指向它。在 GlReverseBack.Finish 的事务里调用（预演除外）。
        internal static void Number(object conn, GlReversePlan plan)
        {
            if (plan.BlueNo.Length == 0)
            {
                plan.BlueNo = ArapVoucherNo.Allocate(conn, "GL");
                plan.BlueAssigned = true;
                GlSql.Exec(conn, "UPDATE GL_accvouch SET coutno_id=?" + GlState.KeyWhere + " AND ISNULL(coutno_id,'')=''",
                    GlSql.With(new object[] { plan.BlueNo }, GlSql.KeyArgs(plan.Source)));
                string n = Rows.Scalar(conn, "SELECT CONVERT(varchar(12), COUNT(*)) n FROM GL_accvouch" + GlState.KeyWhere
                    + " AND coutno_id=?", GlSql.With(GlSql.KeyArgs(plan.Source), new object[] { plan.BlueNo }));
                if ((n ?? "").Trim() != GlReverseReq.Int(plan.SourceRows.Count))
                {
                    throw GlState.Refuse("原凭证状态已变化，未写入");
                }
            }
            plan.RedNo = ArapVoucherNo.Allocate(conn, "GL");
        }
    }
}
