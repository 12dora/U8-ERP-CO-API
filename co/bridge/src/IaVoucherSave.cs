using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 存货核算凭证的外部业务号：照 U8 存货核算制单取号——Ap_CancelNo 的 cType='PZ'、cFlag='IA' 行加一，
    // 号为「年度 4 位 + IA + 13 位补零的序号」（同 U8，如 2026IA0000000000001）。被占用（总账、存货明细账、发出商品明细已有）就往后跳。
    internal static class IaVoucherNo
    {
        public static string Allocate(object conn, int year)
        {
            return ArapVoucherNo.Allocate(conn, "IA", no => PzId(year, no), id => Used(conn, id));
        }

        internal static string PzId(int year, int no)
        {
            string digits = no.ToString(CultureInfo.InvariantCulture);
            return year.ToString("0000", CultureInfo.InvariantCulture) + "IA" + (digits.Length < 13 ? new string('0', 13 - digits.Length) : "") + digits;
        }

        static bool Used(object conn, string id)
        {
            const string Sql = "select top 1 'x' from GL_accvouch where coutno_id=? union all select top 1 'x' from IA_Subsidiary where cPZID=? "
                + "union all select top 1 'x' from IA_EnSubsidiary where cPZID=?";
            return Rows.Scalar(conn, Sql, new object[] { id, id, id }) != null;
        }
    }

    // 凭证保存走 U8 的凭证导入 U8PzInsert.clsPZInsert.Transact（GlSave.Transact，它自己提交）。U8 的存货核算制单界面自己 INSERT
    // GL_accvouch，没有无界面组件；桥用导入器保存，报文带外部来源：voucher_making_system=IA、reserve1=存货核算、reserve2=外部业务号。
    // 按 IA 导入被拒、且拒绝原文说的是制单系统（SystemRejected）、确认没写进去时，才改 GL 再导一次；其余拒绝原样 409 u8_rejected。
    // 不论哪种，回写事务里都按 U8 的样子补齐来源列（IaVoucherBack.PatchGl）。
    internal static class IaVoucherSave
    {
        public const string OutSign = "存货核算";
        public const string System = "IA";
        // 导入器不认制单系统 IA 时拒绝原文里的片段（报文元素名）。测试账套实测 U8 接受 IA，此分支未触发；遇到真实原文时据此调整。
        internal const string SystemRejected = "voucher_making_system";

        public static void Save(WorkContext ctx, IaJob job, IaPlan plan, string maker)
        {
            plan.MaxBefore = ArapVoucherSave.MaxNo(ctx.Conn, plan.Key);
            plan.MakingSystem = System;
            GlReply reply = Import(ctx, job, plan, maker);
            if (reply == null)
            {
                reply = Fallback(ctx, job, plan, maker);
            }
            GlKey key = plan.Key;
            if (reply.No <= 0 || (reply.Period > 0 && reply.Period != key.Period) || (reply.Year > 0 && reply.Year != key.Year))
            {
                CoRows.Note(ctx.Item, "存货核算制单 reply year=" + Int(reply.Year) + " period=" + Int(reply.Period) + " no=" + Int(reply.No));
                throw Unknown(job, plan, "U8 没有返回凭证号或返回的期间不符");
            }
            key.No = reply.No;
            CoRows.Note(ctx.Item, "凭证 " + key.Text() + " " + plan.PzId + " 制单系统 " + plan.MakingSystem);
        }

        // 被拒：只有拒绝原文说的是制单系统时才改 GL 重试，其余原样 409 u8_rejected。
        static GlReply Fallback(WorkContext ctx, IaJob job, IaPlan plan, string maker)
        {
            if (!SystemRefused(plan.Rejected))
            {
                throw Partial(job, new BridgeException(409, "u8_rejected", plan.Rejected.Length > 0 ? plan.Rejected : "U8 拒绝保存凭证"));
            }
            return Retry(ctx, job, plan, maker);
        }

        internal static bool SystemRefused(string said)
        {
            return (said ?? "").IndexOf(SystemRejected, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        static GlReply Retry(WorkContext ctx, IaJob job, IaPlan plan, string maker)
        {
            string said = plan.Rejected;
            CoRows.Note(ctx.Item, "存货核算制单 IA 被拒：" + IaText.Clip(said, 200));
            if (Written(ctx.Conn, plan))
            {
                throw Unknown(job, plan, "U8 回报失败，但总账里已有该外部业务号或新凭证");
            }
            plan.MakingSystem = "GL";
            GlReply reply = Import(ctx, job, plan, maker);
            if (reply == null)
            {
                string again = plan.Rejected;
                throw Partial(job, new BridgeException(409, "u8_rejected", said + (again == said ? "" : "；按 GL 重试：" + again)));
            }
            return reply;
        }

        // U8 拒绝（succeed≠0）返回 null，拒绝原文在 plan.Rejected。
        static GlReply Import(WorkContext ctx, IaJob job, IaPlan plan, string maker)
        {
            GlSource source = new GlSource();
            source.MakingSystem = plan.MakingSystem;
            source.OutSign = OutSign;
            source.OutNo = plan.PzId;
            source.BillType = plan.Src.Count > 0 ? plan.Src[0].BillType : "";
            source.BillId = plan.Src.Count > 0 ? plan.Src[0].BillCode : "";
            source.BillDate = plan.Date;
            source.Operators = plan.Operators;
            string xml = GlXml.Envelope(true, plan.Key, plan.Draft, new string[] { plan.Date, maker }, null, source);
            // 预演在 Prepare 的提交处已经结束；这里再挡一道，保证不调用自提交的导入。
            DryRun.Stop(ctx, "U8PzInsert.Transact");
            try
            {
                return GlSave.Transact(ctx, xml, plan.Key);
            }
            catch (BridgeException ex)
            {
                if (ex.Code == "u8_rejected")
                {
                    plan.Rejected = ex.Message;
                    return null;
                }
                if (ex.Code == "outcome_unknown")
                {
                    throw Unknown(job, plan, "U8 凭证导入调用异常或返回内容无法解析");
                }
                throw Partial(job, ex);
            }
        }

        // U8 回报失败时确认没写进去：外部业务号不在总账里，且本期同类别凭证号没有变大。
        static bool Written(object conn, IaPlan plan)
        {
            if (Rows.Scalar(conn, "select top 1 'x' from GL_accvouch where coutno_id=?", new object[] { plan.PzId }) != null)
            {
                return true;
            }
            return ArapVoucherSave.MaxNo(conn, plan.Key) != plan.MaxBefore;
        }

        // 保存后、回写前：凭证号上的凭证正是这一张（行数、借贷合计、未记账），否则不碰它，504。
        public static void Identify(WorkContext ctx, IaJob job, IaPlan plan)
        {
            bool same;
            try
            {
                same = ArapVoucherSave.Same(ctx.Conn, plan.Key, plan.Draft);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "存货核算制单核对 " + ex.Message);
                throw Saved(job, plan, "核对新凭证失败");
            }
            if (!same)
            {
                throw Saved(job, plan, "U8 返回的凭证号上的凭证与本次制单对不上，未回写存货明细账");
            }
        }

        public static BridgeException Unknown(IaJob job, IaPlan plan, string why)
        {
            GlKey key = plan.Key;
            string at = Int(key.Year) + "年" + Int(key.Period) + "期 " + key.Sign + (key.No > 0 ? "-" + Int(key.No) : " 字");
            return Partial(job, new BridgeException(504, "outcome_unknown", "凭证可能已保存（" + at + "，外部业务号 " + plan.PzId + "），" + why
                + "；存货明细账可能还没有凭证号，请先在总账凭证里按外部业务号核对，不要直接重试"));
        }

        public static BridgeException Saved(IaJob job, IaPlan plan, string why)
        {
            return Partial(job, new BridgeException(504, "outcome_unknown", "凭证已保存（" + plan.Key.Text() + "，外部业务号 " + plan.PzId
                + "），" + why + "；请先在总账凭证里按外部业务号核对，确认后用 ia/voucher/delete 删除再制单"));
        }

        // 第 2 张起失败：前面已完成的凭证保留。结果明确的失败保留原状态码和错误码，结果不明的仍是 504；
        // 两种都在消息里列出已完成的凭证，detail 带 done（已完成的凭证）和 pending（还没制单的单据，只重发这些）。
        public static BridgeException Partial(IaJob job, BridgeException ex)
        {
            if (job.Done == 0 || ex.Detail != null)
            {
                return ex;
            }
            string message = (ex.Code == "outcome_unknown" ? ex.Message : "第 " + Int(job.Done + 1) + " 张凭证没有生成（" + ex.Message + "）")
                + Tail(job);
            return new BridgeException(ex.Status, ex.Code, message).WithDetail(Progress(job));
        }

        // detail：done = [{pz_id, year, period, sign, no, bills:[{type, id, code, rows}]}]，pending = [{type, id}]。
        internal static Dictionary<string, object> Progress(IaJob job)
        {
            List<object> done = new List<object>();
            for (int i = 0; i < job.Done; i++)
            {
                done.Add(IaVoucherView.Brief(job.Plans[i]));
            }
            Dictionary<string, object> detail = new Dictionary<string, object>();
            detail["done"] = done;
            detail["pending"] = job.Pending();
            return detail;
        }

        // 已完成的凭证（第 2 张起失败时）。
        static string Tail(IaJob job)
        {
            StringBuilder done = new StringBuilder();
            for (int i = 0; i < job.Done; i++)
            {
                IaPlan p = job.Plans[i];
                done.Append(i > 0 ? "、" : "").Append(p.Key.Text()).Append("（").Append(p.PzId).Append("）");
            }
            return "。已完成 " + Int(job.Done) + " 张凭证并回写：" + done + "；其余单据未制单（见 detail.pending），核对后只重发这些";
        }

        static string Int(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }
}
