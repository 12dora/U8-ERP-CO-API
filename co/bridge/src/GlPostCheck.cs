using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 记账前的只读检查（请求连接 ctx.Conn，在记账事务之外）。都是 409 state_mismatch，凭证不存在 404。
    // 不再查「有人在 U8 里汇总了还没记」：旧汇总范围同 U8 自己的汇总一样被覆盖，事务里锁表并在记完后清掉本年度范围（GlPostTx）。
    // U8 的 VouchPostAll 自己不查任何状态，只记 GL_mpostcond1 里的凭证；GL_P_JZA 会静默跳过不合格的凭证，
    // 所以桥先逐张查清楚，事务里再核对 U8 汇总出的范围与请求完全一致。
    internal static class GlPostCheck
    {
        const string FirstOpenSql = "SELECT CONVERT(varchar(4), MIN(iperiod)) p FROM GL_mend"
            + " WHERE iyear=? AND iperiod>0 AND iperiod<=12 AND ISNULL(bflag,0)=0";
        const string HasPeriodSql = "SELECT TOP 1 'x' x FROM GL_mend WHERE iyear=? AND iperiod=?";
        const string PriorOpenSql = "SELECT TOP 1 'x' x FROM GL_mend WHERE iyear=? AND iperiod>0 AND iperiod<=12"
            + " AND ISNULL(bflag,0)=0";
        const string MasterSql = "SELECT TOP 1 'x' x FROM gl_ltdata1 WHERE iyear=? AND iperiod=? AND isignseq=? AND ino_id=?"
            + " AND ISNULL(cmaster,N'')<>N''";
        // 照 GL_P_JZA：出纳凭证是 (code.bbank ^ code.bcash)=1 的科目，既是现金又是银行的科目不算。
        const string CashSql = "SELECT TOP 1 'x' x FROM GL_accvouch v JOIN code c ON c.iyear=v.iyear AND c.ccode=v.ccode"
            + " WHERE v.iyear=? AND v.iperiod=? AND v.csign=? AND v.ino_id=? AND (ISNULL(c.bbank,0) ^ ISNULL(c.bcash,0))=1";
        const string ChargedSql = "SELECT TOP 1 'x' x FROM GL_accvouch WHERE iyear=? AND iperiod>=1 AND iperiod<=12 AND ibook=1";

        public static void Run(object conn, GlPostReq req)
        {
            req.Cash = GlState.Option(conn, "bProofSign", false);
            req.Master = GlState.Option(conn, "bMasterSign", false);
            Period(conn, req.Year, req.Period);
            foreach (GlPostItem item in req.Items)
            {
                item.Seq = GlState.SignSeq(conn, item.Sign);
                Voucher(conn, req, item);
            }
            req.Cond = GlPostParse.Cond(req);
        }

        // 同 U8 记账（实测）：只能记本年第一个未结账期间（GL_mend bflag=0 的最小期间）。
        // 记 1 月时上年度（有这一年的话）必须已结账。
        static void Period(object conn, int year, int period)
        {
            string text = Rows.Scalar(conn, FirstOpenSql, new object[] { year });
            int first;
            if (text == null || !int.TryParse(text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out first))
            {
                if (Rows.Scalar(conn, HasPeriodSql, new object[] { year, period }) == null)
                {
                    throw GlState.Refuse("总账没有 " + year.ToString(CultureInfo.InvariantCulture) + " 年第 "
                        + period.ToString(CultureInfo.InvariantCulture) + " 期");
                }
                throw GlState.Refuse("该期间总账已结账");
            }
            if (period < first)
            {
                throw GlState.Refuse("该期间总账已结账");
            }
            if (period > first)
            {
                throw GlState.Refuse("上一会计期间没有结账，不能记账（本年第一个未结账期间是 "
                    + first.ToString(CultureInfo.InvariantCulture) + " 期）");
            }
            if (period == 1 && Rows.Scalar(conn, PriorOpenSql, new object[] { year - 1 }) != null)
            {
                throw GlState.Refuse("上一会计期间没有结账，不能记账（上年度总账未结账）");
            }
        }

        static void Voucher(object conn, GlPostReq req, GlPostItem item)
        {
            GlKey key = req.Key(item);
            GlHead head = GlState.Need(conn, key, false);
            string at = "凭证 " + key.Text() + "：";
            Must(!head.Mixed, at + "各行状态不一致，请在 U8 客户端处理");
            Must(!head.Posted, at + "凭证已记账");
            if (head.Flag == 1)
            {
                // 照 GL_P_JZA（ccheck is null and iflag=1）：作废凭证也记账（只打记账标志，科目总账按 iflag is null 不计入），
                // 不查审核、出纳和主管签字；否则含作废凭证的期间经桥永远记不完、结不了账。
                Must(head.Checker.Length == 0, at + "作废凭证带审核人，U8 不会记账，请在 U8 客户端处理");
                GlState.NotLocked(conn, key);
                return;
            }
            Must(head.Flag != 2, at + "错误凭证，不能记账");
            Must(head.Checker.Length > 0, at + "凭证未审核");
            if (req.Cash && head.Cashier.Length == 0 && Rows.Scalar(conn, CashSql, GlSql.KeyArgs(key)) != null)
            {
                throw GlState.Refuse(at + "出纳凭证未出纳签字");
            }
            if (req.Master && Rows.Scalar(conn, MasterSql, new object[] { key.Year, key.Period, item.Seq, key.No }) == null)
            {
                throw GlState.Refuse(at + "主管会计未签字");
            }
            GlState.NotLocked(conn, key);
        }

        // 本年度还没有记过账（UCPost 的 IsVoucherCharged 为假）时，照 U8 界面先做期初对账和期初试算。
        // 必须在 HttpLoginContext.UserData 已绑定的线程上调用（在事务之外：U8 的期初对账会写 GL_merror）。
        // sqlConn 是交给 U8 的 SqlClient 串（年中建账时查 UFSystem..UA_Period），acc 是账套号。
        public static void FirstPosting(object conn, GlPostNet net, int year, string sqlConn, string acc)
        {
            if (Rows.Scalar(conn, ChargedSql, new object[] { year }) != null)
            {
                return;
            }
            if (!GlState.Option(conn, "bAllowKeepAccouts", false))
            {
                GlPostFirst.Reconcile(net, year);
            }
            GlPostFirst.Trial(net, year, GlPostFirst.TrialPeriod(conn, sqlConn, acc, year));
        }

        static void Must(bool ok, string message)
        {
            if (!ok)
            {
                throw GlState.Refuse(message);
            }
        }
    }
}
