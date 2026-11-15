using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;

namespace U8Co
{
    // 年度首张凭证记账前的两道检查，照 U8 界面 UCPost：
    // 1. 总账选项 bAllowKeepAccouts（期初余额对账不平允许年度首张凭证记账）为假时，期初对账 BalanceRule.QcCheck(0, 年度, -1)，
    //    GLUpper_Lower、GLSum_Ass、GLSum_MultiAss、GLAss_Vouch 四项都要平（UCPost.QCCheck 只看这四项）。
    //    U8 的期初对账本身会清写 GL_merror（对账错误）和 GL_mend 第 0 期的 bpri_check / dpri_check，与在 U8 里点对账相同。
    // 2. 期初试算（UCTrial(0) → BalanceDao.GetTrialBalance(0, 年度)）：各类科目期初余额（借正贷负）合计为 0。
    //    UCTrial.LoadData：建账年度等于操作年度且建账期间大于 1（年中建账）时改用第 1 期（取第 1 期的期末 me）。
    internal static class GlPostFirst
    {
        const string StartSql = "SELECT cValue FROM AccInformation WHERE cSysID='GL' AND cName='dGLStartDate'";
        // 照 ZzPubDao.GetAccDate：日期所在的会计期间（年度、期间号）；查不到按自然年月。
        const string PeriodSql = "SELECT TOP 1 iYear, iId FROM UFSystem..UA_Period WHERE cAcc_id=@a AND @d BETWEEN dBegin AND dEnd";
        static readonly string[] Needed = new string[] { "GLUpper_Lower", "GLSum_Ass", "GLSum_MultiAss", "GLAss_Vouch" };

        public static void Reconcile(GlPostNet net, int year)
        {
            IDictionary result;
            try
            {
                result = net.QcCheck(year);
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                throw GlState.Refuse("年度首张凭证记账前的期初对账在桥内执行失败（" + GlPostTx.FirstLine(ex.Message)
                    + "），请在 U8 客户端记本年第一张凭证");
            }
            List<string> bad = Unbalanced(result);
            if (bad.Count > 0)
            {
                throw GlState.Refuse("期初余额对账不平（" + string.Join("、", bad.ToArray()) + "），请修改后继续记账；"
                    + "总账选项「期初余额对账不平允许年度首张凭证记账」（bAllowKeepAccouts）未开启");
            }
        }

        internal static List<string> Unbalanced(IDictionary result)
        {
            Dictionary<string, bool> seen = new Dictionary<string, bool>(StringComparer.Ordinal);
            if (result != null)
            {
                foreach (DictionaryEntry entry in result)
                {
                    seen[Convert.ToString(entry.Key, CultureInfo.InvariantCulture)] = entry.Value is bool && (bool)entry.Value;
                }
            }
            List<string> bad = new List<string>();
            foreach (string name in Needed)
            {
                bool ok;
                if (!seen.TryGetValue(name, out ok) || !ok)
                {
                    bad.Add(name);
                }
            }
            return bad;
        }

        // 期初试算用的期间：0（期初 mb），年中建账的建账年度用 1。总账选项没有建账日期时 U8 取操作年度 1 月 1 日，即 0。
        public static int TrialPeriod(object conn, string sqlConn, string acc, int year)
        {
            string text = Rows.Scalar(conn, StartSql, new object[0]);
            DateTime start;
            if (text == null || !DateTime.TryParse(text.Trim(), CultureInfo.InvariantCulture, DateTimeStyles.None, out start))
            {
                return 0;
            }
            int beginYear = start.Year;
            int beginPeriod = start.Month;
            List<object[]> rows = GlPostSql.Rows(sqlConn, PeriodSql,
                new System.Data.SqlClient.SqlParameter[] { GlPostSql.P("@a", acc ?? ""), GlPostSql.P("@d", start.Date) });
            if (rows.Count > 0)
            {
                beginYear = GlPostSql.Int(rows[0][0]);
                beginPeriod = GlPostSql.Int(rows[0][1]);
            }
            return beginYear == year && beginPeriod > 1 ? 1 : 0;
        }

        public static void Trial(GlPostNet net, int year, int period)
        {
            decimal sum;
            try
            {
                sum = Sum(net.Trial(period, year));
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                throw GlState.Refuse("年度首张凭证记账前的期初试算在桥内执行失败（" + GlPostTx.FirstLine(ex.Message)
                    + "），请在 U8 客户端记本年第一张凭证");
            }
            if (sum != 0m)
            {
                throw GlState.Refuse("期初试算不平衡（差额 " + sum.ToString("0.00", CultureInfo.InvariantCulture) + "），不能记账");
            }
        }

        // 每行是 TrialsBalanceDto，按类汇总的 sumye（属性或字段，按名字反射取）。
        static decimal Sum(IEnumerable rows)
        {
            if (rows == null)
            {
                throw new BridgeException(503, "u8_unavailable", "U8 总账记账组件不可用：期初试算没有返回结果");
            }
            decimal sum = 0m;
            foreach (object row in rows)
            {
                sum += Money(row);
            }
            return sum;
        }

        static decimal Money(object row)
        {
            if (row == null)
            {
                return 0m;
            }
            Type type = row.GetType();
            object value;
            PropertyInfo prop = type.GetProperty("sumye", BindingFlags.Public | BindingFlags.Instance | BindingFlags.IgnoreCase);
            if (prop != null)
            {
                value = prop.GetValue(row, null);
            }
            else
            {
                FieldInfo field = type.GetField("sumye", BindingFlags.Public | BindingFlags.Instance | BindingFlags.IgnoreCase);
                if (field == null)
                {
                    throw new BridgeException(503, "u8_unavailable", "U8 总账记账组件不可用：期初试算结果没有 sumye");
                }
                value = field.GetValue(row);
            }
            return value == null ? 0m : Convert.ToDecimal(value, CultureInfo.InvariantCulture);
        }
    }
}
