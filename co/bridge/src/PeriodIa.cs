using System;
using System.Collections.Generic;

namespace U8Co
{
    // 存货核算有数据的月份（IA_Subsidiary 或 IA_Summary 该月有行）的月末结账 / 取消结账：
    // 在结账事务里整批执行 sql/ia/close.sql（检查 + IA_Close + 置 bflag_IA）或 sql/ia/unclose.sql（IA_UnClose(下月) + 清 bflag_IA），
    // 标志由脚本改，PeriodClose.Apply 不再改。没有数据的月份仍走只改标志的老路（PeriodCloseChecks / PeriodReopenChecks）。
    // 核算设置的预检、脚本拒绝（THROW 50000–50099）转中文 409 都用 IaRun 的（英文原文只进审计），消息前加期间位置。
    // 表名、列名只来自这里的常量。
    internal static class PeriodIa
    {
        public const string CloseScript = "sql/ia/close.sql";
        public const string UncloseScript = "sql/ia/unclose.sql";
        // 各脚本最后一组诊断计数里必有的键（缺了 SqlScript 报 500，事务回滚）。
        const string CloseKey = "bflag_IA";
        const string UncloseKey = "bflag_IA_M";
        // 开始一个存货核算步骤至少要剩的时间（秒）：min(iaCommandSeconds / 2, 300)。
        const int MinStepSeconds = 300;
        public const string TimeShort = "剩余时间不足，请分段结账（through 一次结到较早的期间）";
        // 取消结账会删下月（同一年度 iMonth+1）的全部 IA_Summary / IA_Subsidiary：下月已有记账（结账写的 24 回冲、61 假退料除外）
        // 或已做期末处理时拒绝，免得把下月的数据一起删掉。
        const string NextBusySql = "SELECT 'x' WHERE EXISTS (SELECT 1 FROM IA_Subsidiary WHERE iYear=? AND iMonth=?"
            + " AND ISNULL(cVouType,N'') NOT IN (N'24',N'61'))"
            + " OR EXISTS (SELECT 1 FROM IA_Summary WHERE iYear=? AND iMonth=? AND ISNULL(iPeriod,0)=1)";

        // 该月存货核算有没有数据（决定走脚本还是只改标志）。
        public static bool HasData(object conn, int year, int period)
        {
            return PeriodCloseChecks.IaRows(conn, "IA_Subsidiary", year, period)
                || PeriodCloseChecks.IaRows(conn, "IA_Summary", year, period);
        }

        // 走脚本前的检查：接口不支持的核算设置；取消结账另查下月。返回拒绝原因，可以做返回 null。
        public static string Check(object conn, bool close, int year, int period)
        {
            string refusal = IaRun.Unsupported(conn);
            if (refusal != null || close)
            {
                return refusal;
            }
            if (PeriodCloseChecks.Has(conn, NextBusySql, year, period + 1, year, period + 1))
            {
                return "下月存货核算已有记账或期末处理数据，取消本月结账会删掉这些数据；请先在下月取消期末处理、恢复记账";
            }
            return null;
        }

        // 执行结账 / 取消结账脚本（IaRun.RunScripts：请求连接、按请求级预算定超时、执行期间放宽看门狗），返回诊断计数（ia_counts）。
        // 剩余时间不到 min(iaCommandSeconds / 2, 300) 时先拒绝（409，整个事务回滚）。拒绝转成 409；
        // 超时由 SqlScript 报 503 ia_timeout，其他错误 500。记账人照 IaRun 的校验（非空、不超过 20 个字符）。
        public static Dictionary<string, object> Run(PeriodRun run, int year, int period)
        {
            WorkContext ctx = run.Ctx;
            string at = PeriodGate.At(PeriodModules.Ia, year, period);
            if (!Enough(IaReq.SecondsLeft(ctx.Item), ctx.Config == null ? 900 : ctx.Config.IaCommandSeconds))
            {
                throw new BridgeException(409, "state_mismatch", at + TimeShort);
            }
            bool close = run.Ask.Close;
            IaArgs args = new IaArgs();
            args.Year = year;
            args.Month = period;
            args.KeepDate = PeriodStock.LastDay(year, period);
            args.Accounter = IaRun.Accounter(ctx);
            args.OnUncosted = "refuse";
            try
            {
                ScriptResult result = IaRun.RunScripts(ctx, new string[] { close ? CloseScript : UncloseScript }, args,
                    close ? CloseKey : UncloseKey);
                return IaRun.Counts(result.Counts);
            }
            catch (ScriptRefusal ex)
            {
                CoRows.Note(ctx.Item, "存货核算脚本 " + PeriodGate.N(ex.Number) + " " + ex.Message);
                throw At(IaRun.Refusal(ex), at);
            }
        }

        // 纯函数，--selftest 用。
        internal static bool Enough(int secondsLeft, int budget)
        {
            return secondsLeft >= Math.Min(budget / 2, MinStepSeconds);
        }

        // IaRun 的 409 前面加上期间位置（through 时看得出是哪一期），状态、代码、字段、提示、detail 不变。纯函数，--selftest 用。
        internal static BridgeException At(BridgeException ex, string at)
        {
            BridgeException moved = new BridgeException(ex.Status, ex.Code, at + ex.Message, ex.Field, ex.Hint);
            return ex.Detail == null ? moved : moved.WithDetail(ex.Detail);
        }
    }
}
