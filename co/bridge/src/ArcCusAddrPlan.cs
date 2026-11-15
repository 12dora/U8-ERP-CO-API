using System;
using System.Collections.Generic;

namespace U8Co
{
    // 客户收货地址的一行（CusDeliverAdd）。名称、单位的 NULL 与空串都读成 ""，比较一律去两端空格。
    internal sealed class ArcCusAddrRow
    {
        public int Id;
        public string Code = "";
        public string Name = "";
        public string Unit = "";
        public bool Default;

        public ArcCusAddrRow Copy()
        {
            ArcCusAddrRow row = new ArcCusAddrRow();
            row.Id = Id;
            row.Code = Code;
            row.Name = Name;
            row.Unit = Unit;
            row.Default = Default;
            return row;
        }

        public Dictionary<string, object> Map()
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["code"] = Code;
            map["name"] = Name;
            map["unit"] = Unit;
            map["default"] = Default;
            return map;
        }
    }

    // 请求。NewCode、Name、Unit、Default 为 null 表示这次不改；Name、Unit 为 "" 表示清空。
    // Exp* 为 null 表示没带 expect（解析时已保证要改的 name / unit / default 都带了）。
    internal sealed class ArcCusAddrAsk
    {
        public string Code;
        public string Address;
        public string NewCode;
        public string Name;
        public string Unit;
        public bool? Default;
        public string ExpName;
        public string ExpUnit;
        public bool? ExpDefault;
    }

    // 一次修改的计划：整个客户的改前、改后各行（按 Id 排序，下标一一对应）和哪些行要写。
    internal sealed class ArcCusAddrPlan
    {
        public int Target;
        public List<ArcCusAddrRow> Before = new List<ArcCusAddrRow>();
        public List<ArcCusAddrRow> After = new List<ArcCusAddrRow>();
        public List<int> Dirty = new List<int>();
        public bool CodeChanges;

        public bool Changed
        {
            get { return Dirty.Count > 0; }
        }

        public string NewAddress
        {
            get { return After[Target].Code; }
        }
    }

    // 一条 UPDATE。Sql 只含本类写死的列名，值全走 Args。
    internal sealed class ArcCusAddrStmt
    {
        public string Sql;
        public object[] Args;
    }

    // 纯函数：定位目标行、expect 校验、新编码查重、默认地址翻转、生成 UPDATE。不连库，--selftest 直接测。
    internal static class ArcCusAddrPlanner
    {
        public static ArcCusAddrPlan Make(ArcCusAddrAsk ask, List<ArcCusAddrRow> rows)
        {
            ArcCusAddrPlan plan = new ArcCusAddrPlan();
            plan.Target = Find(ask, rows);
            ArcCusAddrRow target = rows[plan.Target];
            CheckExpect(ask, target);
            plan.CodeChanges = ask.NewCode != null && !string.Equals(ask.NewCode, target.Code, StringComparison.Ordinal);
            if (plan.CodeChanges)
            {
                CheckTaken(ask.NewCode, rows, plan.Target);
            }
            foreach (ArcCusAddrRow row in rows)
            {
                plan.Before.Add(row);
                plan.After.Add(row.Copy());
            }
            Apply(ask, plan);
            for (int i = 0; i < rows.Count; i++)
            {
                if (!Same(plan.Before[i], plan.After[i]))
                {
                    plan.Dirty.Add(i);
                }
            }
            return plan;
        }

        // 编码比较不区分大小写（库排序规则不区分），也忽略两端空格。
        internal static bool CodeEq(string a, string b)
        {
            return string.Equals((a ?? "").Trim(), (b ?? "").Trim(), StringComparison.OrdinalIgnoreCase);
        }

        static int Find(ArcCusAddrAsk ask, List<ArcCusAddrRow> rows)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                if (CodeEq(rows[i].Code, ask.Address))
                {
                    return i;
                }
            }
            throw new BridgeException(404, "address_not_found", "收货地址不存在：" + ask.Address);
        }

        static void CheckExpect(ArcCusAddrAsk ask, ArcCusAddrRow target)
        {
            List<string> bad = new List<string>();
            if (ask.Name != null && !string.Equals(Norm(ask.ExpName), target.Name, StringComparison.Ordinal))
            {
                bad.Add("地址名称当前是「" + target.Name + "」");
            }
            if (ask.Unit != null && !string.Equals(Norm(ask.ExpUnit), target.Unit, StringComparison.Ordinal))
            {
                bad.Add("收货单位当前是「" + target.Unit + "」");
            }
            if (ask.Default.HasValue && ask.ExpDefault != target.Default)
            {
                bad.Add("默认地址当前是「" + (target.Default ? "是" : "否") + "」");
            }
            if (bad.Count > 0)
            {
                throw new BridgeException(409, "state_mismatch",
                    "收货地址 " + ask.Address + " 与预期不符：" + string.Join("；", bad.ToArray()));
            }
        }

        static void CheckTaken(string newCode, List<ArcCusAddrRow> rows, int target)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                if (i != target && CodeEq(rows[i].Code, newCode))
                {
                    throw new BridgeException(409, "address_code_taken", "收货地址编码已存在：" + newCode);
                }
            }
        }

        static void Apply(ArcCusAddrAsk ask, ArcCusAddrPlan plan)
        {
            ArcCusAddrRow row = plan.After[plan.Target];
            if (ask.NewCode != null)
            {
                row.Code = ask.NewCode;
            }
            if (ask.Name != null)
            {
                row.Name = ask.Name;
            }
            if (ask.Unit != null)
            {
                row.Unit = ask.Unit;
            }
            if (!ask.Default.HasValue)
            {
                return;
            }
            row.Default = ask.Default.Value;
            if (!row.Default)
            {
                return;
            }
            // 设为默认：同一客户其余各行的默认标志全部清掉。
            for (int i = 0; i < plan.After.Count; i++)
            {
                if (i != plan.Target)
                {
                    plan.After[i].Default = false;
                }
            }
        }

        // 只比三个可写字段和默认标志；Id 一致由调用方保证。
        internal static bool Same(ArcCusAddrRow a, ArcCusAddrRow b)
        {
            return string.Equals(a.Code, b.Code, StringComparison.Ordinal)
                && string.Equals(a.Name, b.Name, StringComparison.Ordinal)
                && string.Equals(a.Unit, b.Unit, StringComparison.Ordinal)
                && a.Default == b.Default;
        }

        // 回读核对：行数、Id、各字段与计划的改后完全一致。
        internal static bool Matches(List<ArcCusAddrRow> expected, List<ArcCusAddrRow> actual)
        {
            if (expected.Count != actual.Count)
            {
                return false;
            }
            for (int i = 0; i < expected.Count; i++)
            {
                if (expected[i].Id != actual[i].Id || !Same(expected[i], actual[i]))
                {
                    return false;
                }
            }
            return true;
        }

        // 每个要写的行一条 UPDATE，只含真正变了的列；值为空串写 NULL。条件是行主键加客户编码。
        internal static List<ArcCusAddrStmt> Updates(ArcCusAddrPlan plan, string customer)
        {
            List<ArcCusAddrStmt> list = new List<ArcCusAddrStmt>();
            foreach (int i in plan.Dirty)
            {
                list.Add(Update(plan.Before[i], plan.After[i], customer));
            }
            return list;
        }

        static ArcCusAddrStmt Update(ArcCusAddrRow before, ArcCusAddrRow after, string customer)
        {
            List<string> sets = new List<string>();
            List<object> args = new List<object>();
            Col(sets, args, "cAddCode", before.Code, after.Code, false);
            Col(sets, args, "cDeliverAdd", before.Name, after.Name, true);
            Col(sets, args, "cDeliverUnit", before.Unit, after.Unit, true);
            if (before.Default != after.Default)
            {
                sets.Add("bDefault=?");
                args.Add(after.Default ? 1 : 0);
            }
            args.Add(after.Id);
            args.Add(customer);
            ArcCusAddrStmt stmt = new ArcCusAddrStmt();
            stmt.Sql = "UPDATE CusDeliverAdd SET " + string.Join(", ", sets.ToArray()) + " WHERE id=? AND cCusCode=?";
            stmt.Args = args.ToArray();
            return stmt;
        }

        static void Col(List<string> sets, List<object> args, string column, string was, string now, bool nullable)
        {
            if (string.Equals(was, now, StringComparison.Ordinal))
            {
                return;
            }
            sets.Add(column + "=?");
            args.Add(nullable && now.Length == 0 ? null : now);
        }

        static string Norm(string text)
        {
            return (text ?? "").Trim();
        }

        internal static List<object> Maps(List<ArcCusAddrRow> rows)
        {
            List<object> list = new List<object>();
            foreach (ArcCusAddrRow row in rows)
            {
                list.Add(row.Map());
            }
            return list;
        }
    }
}
