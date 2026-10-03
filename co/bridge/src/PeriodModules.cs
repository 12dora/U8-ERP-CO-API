using System;
using System.Collections.Generic;

namespace U8Co
{
    // 月末结账（periods/close）支持的模块。下标顺序就是 through 的结账顺序：采购、销售、库存、存货核算、应收、应付、总账。
    // GL_mend 上的结账标志列：总账是 bflag（没有 bflag_GL），其余是 bflag_<子系统>。表名、列名只来自这里。
    internal static class PeriodModules
    {
        public const int Count = 7;
        public const int Gl = 6;
        public const int Pu = 0;
        public const int St = 2;
        public const int Ia = 3;

        static readonly string[] Codes = new string[] { "pu", "sa", "st", "ia", "ar", "ap", "gl" };
        static readonly string[] Subs = new string[] { "PU", "SA", "ST", "IA", "AR", "AP", "GL" };
        static readonly string[] Cols = new string[]
        {
            "bflag_PU", "bflag_SA", "bflag_ST", "bflag_IA", "bflag_AR", "bflag_AP", "bflag"
        };
        static readonly string[] Titles = new string[]
        {
            "采购管理", "销售管理", "库存管理", "存货核算", "应收款管理", "应付款管理", "总账"
        };

        // 结账前必须已结账的同期模块（只看已启用的）。U8 只核实了「采购取消结账要求库存、存货、应付未结账」与「总账最后结账」，
        // 其余按 U8 的结账顺序推定（采购、销售 → 库存 → 存货核算 → 应收、应付 → 总账）。取消结账反过来：依赖它的模块已结账时拒绝。
        static readonly int[][] Before = new int[][]
        {
            new int[0],
            new int[0],
            new int[] { 0, 1 },
            new int[] { 0, 1, 2 },
            new int[] { 1 },
            new int[] { 0 },
            new int[] { 0, 1, 2, 3, 4, 5 }
        };

        // 总账结账前还要已结账的其他子系统（已启用的）：U8 总账结账向导列出薪资、固定资产、成本（USZZPUB 的 WA/FA/CA），
        // 桥不结这几个模块（through 也不结），它们没结时总账结账 409。{ 子系统, GL_mend 列, 名称 }。
        static readonly string[][] Extras = new string[][]
        {
            new string[] { "FA", "bflag_FA", "固定资产" },
            new string[] { "WA", "bflag_WA", "薪资管理" },
            new string[] { "CA", "bflag_CA", "成本管理" }
        };

        public const string OrderText = "结账顺序：采购、销售 → 库存 → 存货核算 → 应收、应付 → 总账";
        public const string ModulesHint = "module 只能是 pu、sa、st、ia、ar、ap、gl";

        // 找不到返回 -1。区分大小写（与其他路由的取值一致）。
        public static int IndexOf(string code)
        {
            return code == null ? -1 : Array.IndexOf(Codes, code);
        }

        public static string Code(int mod)
        {
            return Codes[mod];
        }

        public static string Sub(int mod)
        {
            return Subs[mod];
        }

        public static string Col(int mod)
        {
            return Cols[mod];
        }

        public static string Title(int mod)
        {
            return Titles[mod];
        }

        public static int[] Prereqs(int mod)
        {
            return Before[mod];
        }

        // 依赖 mod 的模块（mod 在它们的 Prereqs 里）。纯函数。
        public static List<int> Dependents(int mod)
        {
            List<int> list = new List<int>();
            for (int m = 0; m < Count; m++)
            {
                if (Array.IndexOf(Before[m], mod) >= 0)
                {
                    list.Add(m);
                }
            }
            return list;
        }

        // Extras 里已启用的：{ GL_mend 列, 名称 }，按 Extras 的顺序。纯函数，--selftest 用。
        internal static List<string[]> ExtrasOf(List<string> subs)
        {
            List<string[]> list = new List<string[]>();
            foreach (string[] extra in Extras)
            {
                if (subs.Contains(extra[0]))
                {
                    list.Add(new string[] { extra[1], extra[2] });
                }
            }
            return list;
        }

        // 已启用的子系统号（大写；UFSYSTEM..UA_Account_sub，iYear=9999 有启用日期，同账套体检的 modules）。
        // 读不到 UFSYSTEM 时 409：不知道哪些模块启用了，不能判断总账能不能结账。
        public static List<string> StartedSubs(WorkContext ctx)
        {
            List<Dictionary<string, object>> rows;
            try
            {
                rows = Rows.Query(ctx.Conn, ReportsReadinessSql.ModulesSql, new object[] { ctx.Item.Acc }, 100);
            }
            catch (BridgeException)
            {
                throw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "UA_Account_sub " + ex.Message);
                throw new BridgeException(409, "state_mismatch", "读不到 UFSYSTEM..UA_Account_sub，无法判断哪些模块已启用；"
                    + "桥的 SQL 登录需要 UFSYSTEM 的 SELECT 权限");
            }
            List<string> subs = new List<string>();
            foreach (Dictionary<string, object> row in rows)
            {
                subs.Add(CoRows.Col(row, "s").ToUpperInvariant());
            }
            return subs;
        }

        // 纯函数，--selftest 用。
        internal static bool[] StartedOf(List<string> subs)
        {
            bool[] started = new bool[Count];
            for (int m = 0; m < Count; m++)
            {
                started[m] = subs.Contains(Subs[m]);
            }
            return started;
        }
    }
}
