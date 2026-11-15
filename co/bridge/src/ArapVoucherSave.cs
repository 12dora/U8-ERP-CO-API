using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 制单的凭证保存走 U8 的凭证导入 U8PzInsert.clsPZInsert.Transact（同总账新增 GlSave，它自己提交，不包 CoTrans）。
    // 报文带外部来源：voucher_making_system = AR|AP、reserve1 = coutsign、reserve2 = 外部业务号、每条分录 bill_type / bill_id / bill_date。
    // EAI 模板注释写「导入时必须填 GL」，实测 AR / AP 也能过、来源列都写上；仍保留：U8 拒绝（succeed≠0）且确认没写进去时改用 GL 再导一次；
    // 不论哪种，保存后都在回写事务里按 U8 制单的样子补齐 coutsysname 等来源列（ArapVoucherBack.PatchGl）。
    internal static class ArapVoucherSave
    {
        public static void Save(WorkContext ctx, VoucherPlan plan)
        {
            string maker = GlState.Operator(ctx);
            plan.MaxBefore = MaxNo(ctx.Conn, plan.Key);
            plan.MakingSystem = plan.Doc.Flag;
            GlReply reply = Transact(ctx, plan, plan.MakingSystem, maker);
            if (reply.Succeed != "0")
            {
                reply = Retry(ctx, plan, maker, reply);
            }
            GlKey key = plan.Key;
            if (reply.No <= 0 || (reply.Period > 0 && reply.Period != key.Period) || (reply.Year > 0 && reply.Year != key.Year))
            {
                CoRows.Note(ctx.Item, "制单 reply year=" + Int(reply.Year) + " period=" + Int(reply.Period) + " no=" + Int(reply.No));
                throw Unknown(plan, "U8 没有返回凭证号或返回的期间不符");
            }
            key.No = reply.No;
            CoRows.Note(ctx.Item, "凭证 " + key.Text() + " " + plan.PzId + " 制单系统 " + plan.MakingSystem);
        }

        // 按 AR / AP 导入被拒：确认没写进去之后改 GL 再导一次；还被拒就 409，两次的原文都带回。
        static GlReply Retry(WorkContext ctx, VoucherPlan plan, string maker, GlReply first)
        {
            string said = first.Dsc.Length > 0 ? first.Dsc : "U8 拒绝保存凭证";
            CoRows.Note(ctx.Item, "制单 " + plan.MakingSystem + " 被拒：" + Clip(said));
            if (Written(ctx.Conn, plan))
            {
                throw Unknown(plan, "U8 回报失败，但总账里已有该外部业务号或新凭证");
            }
            plan.MakingSystem = "GL";
            GlReply reply = Transact(ctx, plan, plan.MakingSystem, maker);
            if (reply.Succeed != "0")
            {
                string again = reply.Dsc.Length > 0 ? reply.Dsc : "U8 拒绝保存凭证";
                throw new BridgeException(409, "u8_rejected", said + (again == said ? "" : "；按 GL 重试：" + again));
            }
            return reply;
        }

        static GlReply Transact(WorkContext ctx, VoucherPlan plan, string system, string maker)
        {
            GlSource source = new GlSource();
            source.MakingSystem = system;
            source.OutSign = plan.OutSign;
            source.OutNo = plan.PzId;
            source.BillType = plan.Doc.VType;
            source.BillId = plan.Doc.Code;
            source.BillDate = plan.Date;
            source.Operators = plan.Operators;
            string xml = GlXml.Envelope(true, plan.Key, plan.Draft, new string[] { plan.Date, maker }, null, source);
            // 预演在 Prepare 的提交处已经结束；这里再挡一道，保证不调用自提交的导入。
            DryRun.Stop(ctx, "U8PzInsert.Transact");
            object pz = ComUtil.Create("U8PzInsert.clsPZInsert");
            if (pz == null)
            {
                throw new BridgeException(503, "com_unavailable", "U8 凭证导入组件 U8PzInsert 未注册");
            }
            string text;
            try
            {
                ComUtil.Set(pz, "ToEAICon", ctx.Conn);
                // 登录 by-ref 交给自己提交的导入组件，本次登录不放回缓存（LoginCache）。
                ctx.DropLogin();
                object[] args = new object[] { xml, ctx.Session.Login };
                text = Values.Text(ComUtil.CallRef(pz, "Transact", args, new int[] { 1 }));
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "制单 Transact " + ex.Message);
                throw Unknown(plan, "U8 凭证导入调用异常");
            }
            finally
            {
                ComUtil.Final(pz);
            }
            GlReply reply = GlXml.Parse(text);
            if (reply == null)
            {
                CoRows.Note(ctx.Item, "制单 reply " + Clip(text));
                throw Unknown(plan, "U8 返回内容无法解析");
            }
            return reply;
        }

        // U8 回报失败时确认没写进去：外部业务号不在总账里，且本期同类别凭证号没有变大（导入器可能没写 coutno_id）。
        static bool Written(object conn, VoucherPlan plan)
        {
            if (Rows.Scalar(conn, "select top 1 'x' from GL_accvouch where coutno_id=?", new object[] { plan.PzId }) != null)
            {
                return true;
            }
            return MaxNo(conn, plan.Key) != plan.MaxBefore;
        }

        public static int MaxNo(object conn, GlKey key)
        {
            string raw = Rows.Scalar(conn, "select convert(varchar(12), isnull(max(ino_id),0)) from GL_accvouch where iyear=? and iperiod=? and csign=?",
                new object[] { key.Year, key.Period, key.Sign });
            int value;
            int.TryParse((raw ?? "").Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out value);
            return value;
        }

        // 保存后、回写前：凭证号指向的凭证必须正是这一张（行数、借贷合计、未记账），否则不碰它，504。
        public static void Identify(WorkContext ctx, VoucherPlan plan)
        {
            decimal total = 0;
            foreach (GlLine line in plan.Draft.Lines)
            {
                total += line.Debit;
            }
            Dictionary<string, object> row;
            try
            {
                row = Rows.One(ctx.Conn, "select convert(varchar(12), count(*)) n, " + WriteoffSql.Dec("sum(md)", 2) + " d, "
                    + WriteoffSql.Dec("sum(mc)", 2) + " c, convert(varchar(12), max(isnull(ibook,0))) b from GL_accvouch"
                    + GlState.KeyWhere, GlSql.KeyArgs(plan.Key));
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "制单核对 " + ex.Message);
                throw Saved(plan, "核对新凭证失败");
            }
            bool same = CoRows.AsId(CoRows.Col(row, "n")) == plan.Draft.Lines.Count && CoRows.Col(row, "b") == "0"
                && WriteoffSql.Num(CoRows.Col(row, "d")) == total && WriteoffSql.Num(CoRows.Col(row, "c")) == total;
            if (!same)
            {
                throw Saved(plan, "U8 返回的凭证号上的凭证与本次制单对不上，未回写单据");
            }
        }

        public static BridgeException Unknown(VoucherPlan plan, string why)
        {
            GlKey key = plan.Key;
            string at = Int(key.Year) + "年" + Int(key.Period) + "期 " + key.Sign + (key.No > 0 ? "-" + Int(key.No) : " 字");
            return new BridgeException(504, "outcome_unknown", "凭证可能已保存（" + at + "，外部业务号 " + plan.PzId + "），" + why
                + "；单据的往来明细可能还没有凭证号，请先在总账凭证里按单据号（coutid）/ 外部业务号核对，不要直接重试"
                + "（重试时若总账已有本单没有回写的凭证会 409）");
        }

        public static BridgeException Saved(VoucherPlan plan, string why)
        {
            return new BridgeException(504, "outcome_unknown", "凭证已保存（" + plan.Key.Text() + "，外部业务号 " + plan.PzId + "），" + why
                + "；请先在总账凭证里按单据号（coutid）/ 外部业务号核对，不要重复制单（重试时若总账已有本单没有回写的凭证会 409）");
        }

        static string Clip(string text)
        {
            string value = text ?? "";
            return value.Length > 200 ? value.Substring(0, 200) : value;
        }

        static string Int(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }
}
