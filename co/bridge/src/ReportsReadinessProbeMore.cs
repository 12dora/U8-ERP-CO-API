using System;
using System.Collections.Generic;

namespace U8Co
{
    // 账套体检的后四项：系统启用、以前年度总账结账、质检审批流与操作员人员、缺省档案与编号规则。
    internal static class ReportsReadinessProbeMore
    {
        const string Ok = ReportsReadiness.Ok;
        const string Warn = ReportsReadiness.Warn;
        const string Fail = ReportsReadiness.Fail;

        // 回归要用的子系统：销售、采购、库存、应收、应付、总账、质量、生产订单、物料清单；存货核算没启用只 warn。
        internal static readonly string[] RequiredModules = new string[]
        {
            "SA", "PU", "ST", "AR", "AP", "GL", "QM", "MO", "BO"
        };
        internal static readonly string[] OptionalModules = new string[] { "IA" };

        public static ReadyCheck Modules(ReadyScope s)
        {
            const string hint = "在系统管理（或企业应用平台）→ 系统启用里启用缺的子系统；没有对应的 API";
            List<Dictionary<string, object>> rows = Rows.Query(s.Conn, ReportsReadinessSql.ModulesSql,
                new object[] { s.Acc }, 200);
            List<string> used = new List<string>(rows.Count);
            for (int i = 0; i < rows.Count; i++)
            {
                used.Add(GlSql.Col(rows[i], "s").ToUpperInvariant());
            }
            List<string> missing = Missing(used, RequiredModules);
            List<string> optional = Missing(used, OptionalModules);
            string status = ModulesStatus(missing, optional);
            if (status == Ok)
            {
                return new ReadyCheck("modules", Ok, "已启用 " + string.Join("、", RequiredModules), hint);
            }
            string detail = missing.Count > 0 ? "未启用：" + string.Join("、", missing.ToArray()) : "";
            if (optional.Count > 0)
            {
                detail += (detail.Length > 0 ? "；" : "") + "未启用（可选）：" + string.Join("、", optional.ToArray());
            }
            return new ReadyCheck("modules", status, detail, hint);
        }

        internal static string ModulesStatus(List<string> missing, List<string> optional)
        {
            if (missing.Count > 0)
            {
                return Fail;
            }
            return optional.Count > 0 ? Warn : Ok;
        }

        static List<string> Missing(List<string> used, string[] want)
        {
            List<string> missing = new List<string>();
            for (int i = 0; i < want.Length; i++)
            {
                if (!used.Contains(want[i]))
                {
                    missing.Add(want[i]);
                }
            }
            return missing;
        }

        // 照桥的记账闸门（GlPostCheck.Period）：只能记本年第一个未结账期间；记 1 月时上年度 1–12 期必须都已结账。
        // 按 as_of 与服务器当天中较晚那一天所在的月份判断「本月凭证能不能记账」。
        public static ReadyCheck PriorGlClose(ReadyScope s)
        {
            string target = string.CompareOrdinal(s.AsOf ?? "", s.Today ?? "") >= 0 ? s.AsOf : s.Today;
            DateTime day;
            if (!ReportsReadiness.ParseDay(target, out day))
            {
                return new ReadyCheck("prior_gl_close", Warn, "没有可用的检查日期", PriorHint(0, 0));
            }
            string[] verdict = PostVerdict(s.GlMend(), day.Year, day.Month);
            return new ReadyCheck("prior_gl_close", verdict[0], verdict[1], PriorHint(day.Year, day.Month));
        }

        // 修正提示：through 结到上个月（1 月是上年 12 期）。没有检查日期（year 为 0）时写「本年 / 上月」。纯函数，--selftest 用。
        internal static string PriorHint(int year, int month)
        {
            string fy = "本年";
            string period = "上月（1 月时为上年、12）";
            if (year > 0 && month >= 1 && month <= 12)
            {
                fy = ReportsReadinessProbe.N(month == 1 ? year - 1 : year);
                period = ReportsReadinessProbe.N(month == 1 ? 12 : month - 1);
            }
            return "按顺序把前面的月份结账：U8 总账 → 期末 → 结账，或调用 periods/close"
                + " {\"action\":\"close\",\"through\":true,\"fiscal_year\":" + fy + ",\"period\":" + period + "}"
                + "（只对桥 testAccounts 里的测试账套开放）；否则本月凭证不能记账（gl/vouchers/post 409）";
        }

        // rows：{ 年度, 1–12 期未结账期数, 第一个未结账期间（没有为 0） }。返回 { 状态, 说明 }：
        // 本年第一个未结账期间早于本月，或本月是 1 月而上年有未结账月份：fail（记账会被拒）；
        // 本月已结账（第一个未结账期间晚于本月或全年已结）：fail（「该期间总账已结账」）；
        // 记账不受影响、但更早的年度还有未结账月份：warn；本年在 GL_mend 里没有：warn；其余 ok。
        internal static string[] PostVerdict(List<int[]> rows, int year, int month)
        {
            int[] cur = Find(rows, year);
            string y = ReportsReadinessProbe.N(year);
            string m = ReportsReadinessProbe.N(month);
            if (cur == null)
            {
                return new string[] { Warn, "GL_mend 没有 " + y + " 年（总账可能没有启用或没有建这一年度）" };
            }
            int first = cur[2];
            if (first == 0 || first > month)
            {
                return new string[] { Fail, y + " 年第 " + m + " 期总账已结账，本月凭证不能记账" };
            }
            if (first < month)
            {
                return EarlyOpen(first, month);
            }
            int[] prev = Find(rows, year - 1);
            if (month == 1 && prev != null && prev[1] > 0)
            {
                return new string[] { Fail, "上年度（" + ReportsReadinessProbe.N(year - 1) + "）总账有 "
                    + ReportsReadinessProbe.N(prev[1]) + " 期未结账，1 月凭证不能记账" };
            }
            List<string> older = OlderOpen(rows, month == 1 ? year - 1 : year);
            if (older.Count > 0)
            {
                return new string[] { Warn, "本月（" + y + " 年第 " + m + " 期）可以记账；更早的年度有未结账月份："
                    + string.Join("、", older.ToArray()) };
            }
            return new string[] { Ok, "本月（" + y + " 年第 " + m + " 期）是本年第一个未结账期间，可以记账" };
        }

        // 本年第一个未结账期间早于本月：只差上月是月初的正常时间差（提示），更早则判失败。
        static string[] EarlyOpen(int first, int month)
        {
            if (first == month - 1)
            {
                return new string[] { Warn, "上月（第 " + ReportsReadinessProbe.N(first) + " 期）总账还没结账，结账前本月凭证不能记账" };
            }
            return new string[] { Fail, "本年 " + ReportsReadinessProbe.N(first) + "–" + ReportsReadinessProbe.N(month - 1)
                + " 期总账未结账，本月凭证不能记账" };
        }

        static int[] Find(List<int[]> rows, int year)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                if (rows[i][0] == year)
                {
                    return rows[i];
                }
            }
            return null;
        }

        // 早于 before 的年度里有未结账月份的，记「年度（n 期）」。
        static List<string> OlderOpen(List<int[]> rows, int before)
        {
            List<string> older = new List<string>();
            for (int i = 0; i < rows.Count; i++)
            {
                if (rows[i][0] < before && rows[i][1] > 0)
                {
                    older.Add(ReportsReadinessProbe.N(rows[i][0]) + "（" + ReportsReadinessProbe.N(rows[i][1]) + " 期）");
                }
            }
            return older;
        }

        public static ReadyCheck Workflow(ReadyScope s)
        {
            const string hint = "在 U8 审批流设计器里发布并启用来料检验单（QM03）、产品检验单（QM04）的审批流；"
                + "在 UserHrPersonContro 里把操作员对到人员（造数 steps_people 会做）";
            Dictionary<string, object> row = Rows.One(s.Conn, ReportsReadinessSql.WorkflowSql, new object[] { s.Operator });
            List<string> missing = new List<string>();
            if (GlSql.Int(row, "qm03") == 0)
            {
                missing.Add("QM03 审批流未发布");
            }
            if (GlSql.Int(row, "qm04") == 0)
            {
                missing.Add("QM04 审批流未发布");
            }
            if (GlSql.Int(row, "linked") == 0)
            {
                missing.Add("操作员 " + s.Operator + " 没有对到人员");
            }
            if (missing.Count == 0)
            {
                return new ReadyCheck("workflow", Ok, "QM03、QM04 审批流已启用，操作员已对到人员", hint);
            }
            return new ReadyCheck("workflow", Fail, string.Join("；", missing.ToArray()), hint);
        }

        public static ReadyCheck Defaults(ReadyScope s)
        {
            const string hint = "用档案接口补 currency（本位币在账套参数里）、purchase_type（设一个缺省）、rd_style"
                + "（末级收、发各至少一个）；单据编号规则在 U8 单据编号设置中设置；见 getting-started.md#defaults";
            Dictionary<string, object> row = Rows.One(s.Conn, ReportsReadinessSql.DefaultsSql, new object[0]);
            int[] n = new int[]
            {
                GlSql.Int(row, "home"), GlSql.Int(row, "pt"), GlSql.Int(row, "rd_out"), GlSql.Int(row, "rd_in"),
                GlSql.Int(row, "numbering")
            };
            List<string> missing = DefaultsMissing(n);
            string status = DefaultsStatus(n);
            if (status == Ok)
            {
                return new ReadyCheck("defaults", Ok, "本位币、缺省采购类型、收发类别、单据编号规则齐全", hint);
            }
            return new ReadyCheck("defaults", status, "缺：" + string.Join("、", missing.ToArray()), hint);
        }

        static readonly string[] DefaultsNames = new string[]
        {
            "本位币（foreigncurrency iotherused=-1）", "缺省采购类型（PurchaseType bDefault=1）", "末级发出类收发类别",
            "末级收入类收发类别", "单据编号规则（VoucherNumber）"
        };

        static List<string> DefaultsMissing(int[] n)
        {
            List<string> missing = new List<string>();
            for (int i = 0; i < n.Length; i++)
            {
                if (n[i] == 0)
                {
                    missing.Add(DefaultsNames[i]);
                }
            }
            return missing;
        }

        // n：{ 本位币, 缺省采购类型, 末级发, 末级收, 编号规则 } 的行数。没有本位币 fail，其余缺了 warn。
        internal static string DefaultsStatus(int[] n)
        {
            if (n[0] == 0)
            {
                return Fail;
            }
            for (int i = 1; i < n.Length; i++)
            {
                if (n[i] == 0)
                {
                    return Warn;
                }
            }
            return Ok;
        }
    }
}
