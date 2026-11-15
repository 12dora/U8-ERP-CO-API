using System;
using System.Collections.Generic;

namespace U8Co
{
    // 一次新增或删除的计划：整个客户的改前、改后各行。Insert 非空表示要插一行（它是 After 的最后一行，Id 未知为 0）；
    // Remove 非空表示要删一行（它不在 After 里）；Dirty 是新增设默认时要取消默认的已有行（Before / After 下标一一对应）。
    internal sealed class ArcCusAddrRowsPlan
    {
        public List<ArcCusAddrRow> Before = new List<ArcCusAddrRow>();
        public List<ArcCusAddrRow> After = new List<ArcCusAddrRow>();
        public List<int> Dirty = new List<int>();
        public ArcCusAddrRow Insert;
        public ArcCusAddrRow Remove;
        // 响应里的 address：库里实际存的编码。
        public string Address;

        public bool Changed
        {
            get { return Insert != null || Remove != null || Dirty.Count > 0; }
        }
    }

    // 纯函数：新增判重 / 幂等重放、删除的 expect 与默认地址校验、生成 SQL。不连库，--selftest 直接测。
    internal static class ArcCusAddrRowsPlanner
    {
        const string InsertSql = "INSERT INTO CusDeliverAdd (cCusCode, cAddCode, cDeliverAdd, cDeliverUnit, bDefault,"
            + " cCusAddressGUID) VALUES (?, ?, ?, ?, ?, NEWID())";
        const string DeleteSql = "DELETE FROM CusDeliverAdd WHERE id=? AND cCusCode=?";

        // 新增。编码已存在：名称、单位、默认标志全一致是幂等重放（不写），否则 409 address_code_taken；
        // 编码不存在而名称已被同客户另一行占用 409 address_name_taken；设默认时其余行取消默认。
        public static ArcCusAddrRowsPlan MakeAdd(ArcCusAddrRowsAsk ask, List<ArcCusAddrRow> rows)
        {
            ArcCusAddrRowsPlan plan = Start(rows);
            int at = Find(rows, ask.Address);
            if (at >= 0)
            {
                CheckReplay(ask, rows[at]);
                plan.Address = rows[at].Code;
                return plan;
            }
            CheckNameFree(ask, rows);
            ArcCusAddrRow row = new ArcCusAddrRow();
            row.Code = ask.Address;
            row.Name = ask.Name;
            row.Unit = ask.Unit;
            row.Default = ask.Default;
            plan.Insert = row;
            plan.Address = row.Code;
            if (row.Default)
            {
                for (int i = 0; i < plan.After.Count; i++)
                {
                    if (plan.After[i].Default)
                    {
                        plan.After[i].Default = false;
                        plan.Dirty.Add(i);
                    }
                }
            }
            plan.After.Add(row);
            return plan;
        }

        // 删除。地址不存在 404；expect 不符 409 state_mismatch；是默认地址且客户还有别的行 409 address_is_default
        // （调用方先把默认挪走；只剩这一行时允许）。被单据引用的检查（address_in_use）要查库，由调用方在计划之后做，
        // 所以既是默认又被引用的地址先报 address_is_default：便宜的纯内存检查在前，避免白扫一遍元数据。
        public static ArcCusAddrRowsPlan MakeDelete(ArcCusAddrRowsAsk ask, List<ArcCusAddrRow> rows)
        {
            int at = Find(rows, ask.Address);
            if (at < 0)
            {
                throw new BridgeException(404, "address_not_found", "收货地址不存在：" + ask.Address);
            }
            ArcCusAddrRow target = rows[at];
            CheckExpect(ask, target);
            if (target.Default && rows.Count > 1)
            {
                throw new BridgeException(409, "address_is_default",
                    "收货地址 " + target.Code + " 是默认地址，请先把默认地址改到别的地址再删除");
            }
            ArcCusAddrRowsPlan plan = Start(rows);
            plan.After.RemoveAt(at);
            plan.Remove = target;
            plan.Address = target.Code;
            return plan;
        }

        static ArcCusAddrRowsPlan Start(List<ArcCusAddrRow> rows)
        {
            ArcCusAddrRowsPlan plan = new ArcCusAddrRowsPlan();
            foreach (ArcCusAddrRow row in rows)
            {
                plan.Before.Add(row);
                plan.After.Add(row.Copy());
            }
            return plan;
        }

        static int Find(List<ArcCusAddrRow> rows, string address)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                if (ArcCusAddrPlanner.CodeEq(rows[i].Code, address))
                {
                    return i;
                }
            }
            return -1;
        }

        static void CheckReplay(ArcCusAddrRowsAsk ask, ArcCusAddrRow row)
        {
            if (string.Equals(row.Name, ask.Name, StringComparison.Ordinal)
                && string.Equals(row.Unit, ask.Unit, StringComparison.Ordinal) && row.Default == ask.Default)
            {
                return;
            }
            throw new BridgeException(409, "address_code_taken", "收货地址编码已存在且内容不同：" + row.Code
                + "（当前名称「" + row.Name + "」、收货单位「" + row.Unit + "」、默认" + (row.Default ? "是" : "否") + "）");
        }

        static void CheckNameFree(ArcCusAddrRowsAsk ask, List<ArcCusAddrRow> rows)
        {
            foreach (ArcCusAddrRow row in rows)
            {
                if (string.Equals(row.Name, ask.Name, StringComparison.Ordinal))
                {
                    throw new BridgeException(409, "address_name_taken",
                        "地址名称已被收货地址 " + row.Code + " 使用：" + ask.Name);
                }
            }
        }

        static void CheckExpect(ArcCusAddrRowsAsk ask, ArcCusAddrRow target)
        {
            List<string> bad = new List<string>();
            if (!string.Equals(ask.ExpName, target.Name, StringComparison.Ordinal))
            {
                bad.Add("地址名称当前是「" + target.Name + "」");
            }
            if (!string.Equals(ask.ExpUnit, target.Unit, StringComparison.Ordinal))
            {
                bad.Add("收货单位当前是「" + target.Unit + "」");
            }
            if (ask.ExpDefault != target.Default)
            {
                bad.Add("默认地址当前是「" + (target.Default ? "是" : "否") + "」");
            }
            if (bad.Count > 0)
            {
                throw new BridgeException(409, "state_mismatch",
                    "收货地址 " + target.Code + " 与预期不符：" + string.Join("；", bad.ToArray()));
            }
        }

        // 新增设默认时，其余行取消默认的 UPDATE（复用改地址的生成器，只含 bDefault）。
        internal static List<ArcCusAddrStmt> Flips(ArcCusAddrRowsPlan plan, string customer)
        {
            ArcCusAddrPlan flips = new ArcCusAddrPlan();
            flips.Before = plan.Before;
            flips.After = plan.After;
            flips.Dirty = plan.Dirty;
            return ArcCusAddrPlanner.Updates(flips, customer);
        }

        // 名称、单位为空写 NULL；cCusAddressGUID 由数据库 NEWID() 生成；id 是自增，不写。
        internal static ArcCusAddrStmt Insert(ArcCusAddrRowsPlan plan, string customer)
        {
            ArcCusAddrRow row = plan.Insert;
            ArcCusAddrStmt stmt = new ArcCusAddrStmt();
            stmt.Sql = InsertSql;
            stmt.Args = new object[]
            {
                customer, row.Code, row.Name.Length == 0 ? null : row.Name,
                row.Unit.Length == 0 ? null : row.Unit, row.Default ? 1 : 0
            };
            return stmt;
        }

        internal static ArcCusAddrStmt Delete(ArcCusAddrRowsPlan plan, string customer)
        {
            ArcCusAddrStmt stmt = new ArcCusAddrStmt();
            stmt.Sql = DeleteSql;
            stmt.Args = new object[] { plan.Remove.Id, customer };
            return stmt;
        }

        // 新增后的回读核对：行数一致；已有行按 Id 对上且各字段一致；新行 Id 未知，按编码找，字段一致且 Id 大于 0。
        internal static bool MatchesAdd(List<ArcCusAddrRow> expected, List<ArcCusAddrRow> actual)
        {
            if (expected.Count != actual.Count)
            {
                return false;
            }
            Dictionary<int, ArcCusAddrRow> byId = new Dictionary<int, ArcCusAddrRow>();
            foreach (ArcCusAddrRow row in actual)
            {
                byId[row.Id] = row;
            }
            foreach (ArcCusAddrRow want in expected)
            {
                ArcCusAddrRow got = want.Id == 0 ? ByCode(actual, want.Code) : Pick(byId, want.Id);
                if (got == null || got.Id <= 0 || !ArcCusAddrPlanner.Same(want, got))
                {
                    return false;
                }
            }
            return true;
        }

        static ArcCusAddrRow Pick(Dictionary<int, ArcCusAddrRow> byId, int id)
        {
            ArcCusAddrRow row;
            return byId.TryGetValue(id, out row) ? row : null;
        }

        static ArcCusAddrRow ByCode(List<ArcCusAddrRow> rows, string code)
        {
            int at = Find(rows, code);
            return at < 0 ? null : rows[at];
        }
    }

    // 客户收货地址新增 / 删除的受控 SQL：一个事务里 UPDLOCK, HOLDLOCK, ROWLOCK 锁住该客户的全部地址行（键范围锁，
    // 挡住并发新增同编码或默认地址），按计划写，预演到此回滚，写后同事务重读整客户核对。该表没有 rowversion，
    // 锁加判重（新增）/ expect（删除）就是并发条件。
    internal static class ArcCusAddrRowsSql
    {
        public static ApiResult RunAdd(WorkContext ctx, ArcCusAddrRowsAsk ask)
        {
            string note = ArcCusAddrRows.AddOp + " " + ask.Code + " " + ask.Address;
            return ArcCustomerRow.Transact(ctx, note, conn => ApplyAdd(conn, ctx, ask));
        }

        public static ApiResult RunDelete(WorkContext ctx, ArcCusAddrRowsAsk ask)
        {
            string note = ArcCusAddrRows.DeleteOp + " " + ask.Code + " " + ask.Address;
            return ArcCustomerRow.Transact(ctx, note, conn => ApplyDelete(conn, ctx, ask));
        }

        static ApiResult ApplyAdd(object conn, WorkContext ctx, ArcCusAddrRowsAsk ask)
        {
            NeedCustomer(conn, ask.Code);
            ArcCusAddrRowsPlan plan = ArcCusAddrRowsPlanner.MakeAdd(ask, ArcCusAddrSql.Load(conn, ask.Code));
            if (DryRun.Active)
            {
                DryRun.StopWith(ctx, ArcCusAddrRows.DryBody(ask, plan));
            }
            if (!plan.Changed)
            {
                return ApiResult.Ok(ArcCusAddrRows.OkBody(ask, plan, false));
            }
            foreach (ArcCusAddrStmt stmt in ArcCusAddrRowsPlanner.Flips(plan, ask.Code))
            {
                GlSql.Exec(conn, stmt.Sql, stmt.Args);
            }
            ArcCusAddrStmt insert = ArcCusAddrRowsPlanner.Insert(plan, ask.Code);
            GlSql.Exec(conn, insert.Sql, insert.Args);
            Confirm(conn, ask, plan, "写入");
            return ApiResult.Ok(ArcCusAddrRows.OkBody(ask, plan, true));
        }

        static ApiResult ApplyDelete(object conn, WorkContext ctx, ArcCusAddrRowsAsk ask)
        {
            NeedCustomer(conn, ask.Code);
            ArcCusAddrRowsPlan plan = ArcCusAddrRowsPlanner.MakeDelete(ask, ArcCusAddrSql.Load(conn, ask.Code));
            CheckRefs(conn, ask.Code, plan.Address);
            if (DryRun.Active)
            {
                DryRun.StopWith(ctx, ArcCusAddrRows.DryBody(ask, plan));
            }
            ArcCusAddrStmt delete = ArcCusAddrRowsPlanner.Delete(plan, ask.Code);
            GlSql.Exec(conn, delete.Sql, delete.Args);
            Confirm(conn, ask, plan, "删除");
            return ApiResult.Ok(ArcCusAddrRows.OkBody(ask, plan, true));
        }

        static void NeedCustomer(object conn, string customer)
        {
            if (Rows.One(conn, ArcCusAddrSql.CustomerSql, new object[] { customer }) == null)
            {
                throw new BridgeException(404, "not_found", "档案不存在");
            }
        }

        static void CheckRefs(object conn, string customer, string address)
        {
            List<string> hits = ArcCusAddrRefs.Hits(conn, customer, address);
            if (hits.Count > 0)
            {
                throw new BridgeException(409, "address_in_use", ArcCusAddrRefs.Message(hits, "删除"));
            }
        }

        // 同一事务内重读整个客户的地址行，必须与计划的改后完全一致，否则 504（结果未知）。
        static void Confirm(object conn, ArcCusAddrRowsAsk ask, ArcCusAddrRowsPlan plan, string what)
        {
            List<ArcCusAddrRow> actual = ArcCusAddrSql.Load(conn, ask.Code);
            bool ok = plan.Insert != null
                ? ArcCusAddrRowsPlanner.MatchesAdd(plan.After, actual)
                : ArcCusAddrPlanner.Matches(plan.After, actual);
            if (!ok)
            {
                throw new BridgeException(504, "outcome_unknown",
                    "客户 " + ask.Code + " 的收货地址 " + ask.Address + " 已" + what + "，但回读与写入不一致，结果未知");
            }
        }
    }
}
