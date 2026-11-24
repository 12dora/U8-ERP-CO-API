using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 制单计划：定科目和摘要、按合并方式分凭证、拼分录、照总账新增的规则校验分录（400 改 409：设置或单据的问题，不是请求的），
    // 凭证类别、制单日期、制单序时。逐张制单且按月份选时，某张单据拼不出凭证（缺科目、辅助项等）就跳过它，原因进 skipped；
    // 按单据选或合并制单时整笔 409。
    internal static class IaVoucherPlan
    {
        // 没给 sign 时：账套最近一张存货核算凭证的类别（U8 制单界面记住上次选的类别），没有时「转」。
        const string LastSignSql = "select top 1 csign from GL_accvouch where coutsysname=N'IA' "
            + "order by iyear desc, iperiod desc, dbill_date desc, ino_id desc";
        const string DefaultSign = "转";

        public static void Make(object conn, IaJob job)
        {
            DateCheck(conn, job);
            job.Sign = Sign(conn, job.Ask.Sign);
            IaVoucherAcct acct = IaVoucherAcct.Load(conn, job.Year);
            IaVoucherLines.Cache cache = new IaVoucherLines.Cache();
            List<IaBill> eligible = new List<IaBill>();
            foreach (List<IaBill> unit in IaVoucherPair.Units(job.Bills))
            {
                if (unit[0].Skip.Length == 0 && Prepare(conn, acct, job, unit))
                {
                    eligible.AddRange(unit);
                }
            }
            if (job.Ask.All)
            {
                job.Plans.Add(Plan(conn, job, eligible, cache));
            }
            else
            {
                foreach (List<IaBill> unit in IaVoucherPair.Units(eligible))
                {
                    IaPlan plan = TryPlan(conn, job, unit, cache);
                    if (plan != null)
                    {
                        job.Plans.Add(plan);
                    }
                }
            }
            if (job.Plans.Count == 0)
            {
                throw Nothing(job);
            }
        }

        // 全部单据都被跳过：409，带第一张的原因（错误体里没有 skipped）。
        static BridgeException Nothing(IaJob job)
        {
            IaBill first = job.Bills.Find(b => b.Skip.Length > 0);
            return IaVoucherLoad.Refuse("没有可制单的单据" + (first != null ? "；如 " + first.Label() + "：" + first.Skip : ""));
        }

        // 一个制单单元（一张单据，或成对业务的两边）：防重复制单（总账里已有没回写的凭证），定各行的科目和摘要。
        // 按月份逐张制单时不合格就整个单元跳过（false），其余情况 409。
        static bool Prepare(object conn, IaVoucherAcct acct, IaJob job, List<IaBill> unit)
        {
            try
            {
                foreach (IaBill bill in unit)
                {
                    IaVoucherBack.NoOrphan(conn, bill);
                    foreach (IaRow row in bill.Rows)
                    {
                        acct.Resolve(bill, row);
                        row.Digest = Digest(job.Ask.Digest, row);
                    }
                }
                return true;
            }
            catch (BridgeException ex)
            {
                return Skip(job, unit, ex);
            }
        }

        static IaPlan TryPlan(object conn, IaJob job, List<IaBill> unit, IaVoucherLines.Cache cache)
        {
            try
            {
                return Plan(conn, job, unit, cache);
            }
            catch (BridgeException ex)
            {
                Skip(job, unit, ex);
                return null;
            }
        }

        // 可跳过时把原因记在单元的每张单据上（返回 false）；否则原样抛出。
        static bool Skip(IaJob job, List<IaBill> unit, BridgeException ex)
        {
            if (!Skippable(job, ex))
            {
                throw ex;
            }
            foreach (IaBill bill in unit)
            {
                bill.Skip = ex.Message;
            }
            return false;
        }

        static bool Skippable(IaJob job, BridgeException ex)
        {
            return job.Ask.Month > 0 && !job.Ask.All && ex.Status == 409;
        }

        // 摘要：调用方给了就用；否则单据备注，没有备注时单据类型名，成对业务是业务单据名（调拨单、形态转换单等）
        // （同 U8：IA_Subsidiary.cPZdigest 回测核对）。
        internal static string Digest(string asked, IaRow row)
        {
            string memo = (row.Memo ?? "").Trim();
            string name = row.Pair.Length > 0 ? IaType.PairDigest(row.Pair) : row.Type.Name;
            string text = asked.Length > 0 ? asked : (memo.Length > 0 ? memo : name);
            return IaText.Clip(text, IaVoucherReq.DigestMax);
        }

        static IaPlan Plan(object conn, IaJob job, List<IaBill> bills, IaVoucherLines.Cache cache)
        {
            IaPlan plan = new IaPlan();
            plan.Bills = bills;
            plan.Date = job.Date;
            plan.Key.Year = job.Year;
            plan.Key.Period = Month(job.Date);
            plan.Key.Sign = job.Sign;
            plan.Seq = GlState.SignSeq(conn, job.Sign);
            List<IaLine> lines;
            try
            {
                lines = IaVoucherLines.Build(conn, plan, job.Year, cache);
            }
            catch (BridgeException ex)
            {
                throw Named(bills, ex);
            }
            Fill(plan, lines);
            Validate(conn, plan);
            GlCheck.Order(conn, plan.Key, plan.Date, false);
            return plan;
        }

        // 逐张制单时拒绝原因前加单据（合计为 0、行数超限这类不带单据名的）；成对业务带第一张。
        static BridgeException Named(List<IaBill> bills, BridgeException ex)
        {
            if (IaVoucherPair.Units(bills).Count != 1 || ex.Message.StartsWith("单据 ", StringComparison.Ordinal))
            {
                return ex;
            }
            return new BridgeException(ex.Status, ex.Code, "单据 " + bills[0].Label() + "：" + ex.Message);
        }

        static void Fill(IaPlan plan, List<IaLine> lines)
        {
            plan.Draft.Sign = plan.Key.Sign;
            plan.Draft.Date = plan.Date;
            plan.Draft.Attachments = Math.Min(9999, plan.Bills.Count);
            for (int i = 0; i < lines.Count; i++)
            {
                plan.Draft.Lines.Add(lines[i].Line);
                plan.Src.Add(lines[i].Src);
                if (lines[i].Op.Length > 0)
                {
                    plan.Operators[i + 1] = lines[i].Op;
                }
            }
        }

        // 科目、辅助项档案照总账新增（GlCheck.Validate）查一遍；它的 400 是设置或单据的问题，改 409。
        static void Validate(object conn, IaPlan plan)
        {
            try
            {
                GlCheck.Validate(conn, plan.Key.Year, plan.Draft);
            }
            catch (BridgeException ex)
            {
                if (ex.Status != 400)
                {
                    throw;
                }
                string who = IaVoucherPair.Units(plan.Bills).Count == 1 ? "单据 " + plan.Bills[0].Label() + "：" : "";
                throw IaVoucherLoad.Refuse(who + "凭证分录不合格：" + ex.Message);
            }
        }

        // 制单日期：缺省登录日期；在登录年度内、总账该期间未结账、不早于所选单据的记账月份。
        static void DateCheck(object conn, IaJob job)
        {
            DateTime day = DateTime.ParseExact(job.Date, "yyyy-MM-dd", CultureInfo.InvariantCulture);
            if (day.Year != job.Year)
            {
                throw IaVoucherLoad.Refuse("制单日期必须在登录年度 " + IaVoucherReq.Int(job.Year) + " 内（请求的 date 是登录日期，制单日期用 voucher_date）");
            }
            GlState.PeriodOpen(conn, day.Year, day.Month);
            foreach (IaBill bill in job.Bills)
            {
                foreach (IaRow row in bill.Skip.Length > 0 ? new List<IaRow>() : bill.Rows)
                {
                    if (row.Month > day.Month)
                    {
                        throw IaVoucherLoad.Refuse("制单日期 " + job.Date + " 早于单据 " + bill.Label() + " 的记账月份（" + IaVoucherReq.Int(row.Month) + " 月）");
                    }
                }
            }
        }

        internal static int Month(string date)
        {
            return DateTime.ParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture).Month;
        }

        // 缺省类别：最近一张存货核算凭证的类别，没有时「转」；不在锁定的常见类别里时 409（锁键入队前就定了）。
        static string Default(object conn)
        {
            string sign = (Rows.Scalar(conn, LastSignSql, new object[0]) ?? "").Trim();
            sign = sign.Length > 0 ? sign : DefaultSign;
            if (Array.IndexOf(IaVoucherReq.Signs, sign) < 0)
            {
                throw IaVoucherLoad.Refuse("缺省凭证类别 " + sign + "（最近一张存货核算凭证的类别）不在常见类别里，请用 sign 指定");
            }
            return sign;
        }

        static string Sign(object conn, string asked)
        {
            string sign = asked.Length > 0 ? asked : Default(conn);
            string adjust = Rows.Scalar(conn, "select convert(varchar(4), isnull(iAdjustFlag,0)) from dsign where csign=?", new object[] { sign });
            if (adjust == null || adjust.Trim() == "1")
            {
                throw IaVoucherLoad.Refuse("凭证类别 " + sign + " 不存在或是调整期凭证类别" + (asked.Length == 0 ? "，请用 sign 指定" : ""));
            }
            return sign;
        }
    }
}
