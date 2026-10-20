using System;
using System.Collections.Generic;

namespace U8Co
{
    // 客户收货地址的受控 SQL：一个事务里 UPDLOCK, HOLDLOCK, ROWLOCK 锁住该客户的全部地址行（键范围锁，挡住并发新增同编码或默认地址），校验 expect / 新编码 / 引用，
    // 预演到此回滚，否则逐行 UPDATE（默认地址翻转同事务），回读整客户核对。该表没有 rowversion，锁加 expect 就是并发条件。
    internal static class ArcCusAddrSql
    {
        const string CustomerSql = "SELECT cCusCode AS code FROM Customer WHERE cCusCode=?";
        const string RowsSql = "SELECT id AS id, LTRIM(RTRIM(ISNULL(cAddCode,''))) AS code,"
            + " LTRIM(RTRIM(ISNULL(cDeliverAdd,''))) AS name,"
            + " LTRIM(RTRIM(ISNULL(cDeliverUnit,''))) AS unit,"
            + " CONVERT(int, ISNULL(bDefault,0)) AS dflt"
            + " FROM CusDeliverAdd WITH (UPDLOCK, HOLDLOCK, ROWLOCK) WHERE cCusCode=? ORDER BY id";
        const int RowsMax = 2000;

        public static ApiResult Run(WorkContext ctx, ArcCusAddrAsk ask)
        {
            string note = "customer_address " + ask.Code + " " + ask.Address
                + (ask.NewCode == null ? "" : ">" + ask.NewCode);
            return ArcCustomerRow.Transact(ctx, note, conn => Apply(conn, ctx, ask));
        }

        static ApiResult Apply(object conn, WorkContext ctx, ArcCusAddrAsk ask)
        {
            if (Rows.One(conn, CustomerSql, new object[] { ask.Code }) == null)
            {
                throw new BridgeException(404, "not_found", "档案不存在");
            }
            ArcCusAddrPlan plan = ArcCusAddrPlanner.Make(ask, Load(conn, ask.Code));
            if (plan.CodeChanges)
            {
                CheckRefs(conn, ask);
            }
            if (DryRun.Active)
            {
                DryRun.StopWith(ctx, ArcCusAddr.DryBody(ask, plan));
            }
            if (!plan.Changed)
            {
                return ApiResult.Ok(ArcCusAddr.OkBody(ask, plan, false));
            }
            foreach (ArcCusAddrStmt stmt in ArcCusAddrPlanner.Updates(plan, ask.Code))
            {
                GlSql.Exec(conn, stmt.Sql, stmt.Args);
            }
            Confirm(conn, ask, plan);
            return ApiResult.Ok(ArcCusAddr.OkBody(ask, plan, true));
        }

        static void CheckRefs(object conn, ArcCusAddrAsk ask)
        {
            List<string> hits = ArcCusAddrRefs.Hits(conn, ask.Code, ask.Address);
            if (hits.Count > 0)
            {
                throw new BridgeException(409, "address_in_use", ArcCusAddrRefs.Message(hits));
            }
        }

        static List<ArcCusAddrRow> Load(object conn, string customer)
        {
            List<Dictionary<string, object>> raw = Rows.Query(conn, RowsSql, new object[] { customer }, RowsMax);
            if (raw.Count >= RowsMax)
            {
                throw new BridgeException(500, "internal", "客户 " + customer + " 的收货地址过多，无法处理");
            }
            List<ArcCusAddrRow> list = new List<ArcCusAddrRow>();
            foreach (Dictionary<string, object> r in raw)
            {
                ArcCusAddrRow row = new ArcCusAddrRow();
                row.Id = GlSql.Int(r, "id");
                row.Code = GlSql.Col(r, "code");
                row.Name = GlSql.Col(r, "name");
                row.Unit = GlSql.Col(r, "unit");
                row.Default = GlSql.Int(r, "dflt") != 0;
                list.Add(row);
            }
            return list;
        }

        // 同一事务内重读整个客户的地址行，必须与计划的改后完全一致，否则 504（和模板一致，结果未知）。
        static void Confirm(object conn, ArcCusAddrAsk ask, ArcCusAddrPlan plan)
        {
            if (!ArcCusAddrPlanner.Matches(plan.After, Load(conn, ask.Code)))
            {
                throw new BridgeException(504, "outcome_unknown",
                    "客户 " + ask.Code + " 的收货地址 " + ask.Address + " 已写入，但回读与写入不一致，结果未知");
            }
        }
    }
}
