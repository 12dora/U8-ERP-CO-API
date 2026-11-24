using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 客户开票单位新增 / 删除 / 设默认的受控 SQL：一个事务里先 UPDLOCK 锁 Customer 行（不存在 404），再 UPDLOCK, HOLDLOCK, ROWLOCK
    // 锁该客户的全部开票单位行（该表没有 rowversion 也没有唯一索引，锁加判重 / expect 就是并发条件），按计划写，
    // 预演到此回滚，写后同事务重读整客户核对。默认开票单位变化时按 pubufts 乐观锁改 Customer.cInvoiceCompany 并写修改人、修改日期。
    internal static class ArcCusInvoiceSql
    {
        internal static readonly string LockSql = ArcCustomerRow.LockSql(
            "LTRIM(RTRIM(cCusCode)) AS ccode, LTRIM(RTRIM(ISNULL(cInvoiceCompany,''))) AS header");
        internal const string CompanySql = "SELECT LTRIM(RTRIM(cCusCode)) AS code FROM Customer WHERE cCusCode=?";
        internal const string RowsSql = "SELECT CONVERT(varchar(20), autoid) AS id, LTRIM(RTRIM(ISNULL(cinvoicecompany,''))) AS code,"
            + " CONVERT(int, ISNULL(bdefault,0)) AS dflt"
            + " FROM sa_invoicecustomers WITH (UPDLOCK, HOLDLOCK, ROWLOCK) WHERE ccuscode=? ORDER BY autoid";
        const int RowsMax = 2000;

        public static ApiResult Run(WorkContext ctx, string op, ArcCusInvoiceAsk ask)
        {
            return ArcCustomerRow.Transact(ctx, op + " " + ask.Code, conn => Apply(conn, ctx, op, ask));
        }

        static ApiResult Apply(object conn, WorkContext ctx, string op, ArcCusInvoiceAsk ask)
        {
            Dictionary<string, object> customer = ArcCustomerRow.Need(conn, LockSql, ask.Customer);
            string ts = ArcCustomerRow.Cell(customer, "ts");
            ask.Customer = ArcCustomerRow.Cell(customer, "ccode");
            if (op != ArcCusInvoice.DeleteOp)
            {
                ask.Company = NeedCompany(conn, ask.Company);
            }
            CheckTs(ask, ts);
            ArcCusInvoicePlan plan = Make(op, ask, Load(conn, ask.Customer));
            if (DryRun.Active)
            {
                DryRun.StopWith(ctx, ArcCusInvoice.DryBody(ask, plan));
            }
            if (!plan.Changed)
            {
                return ApiResult.Ok(ArcCusInvoice.OkBody(ask, plan, false));
            }
            bool header = plan.Header != null && !ArcCustomerRow.Same(plan.Header, ArcCustomerRow.Cell(customer, "header"));
            Write(conn, ctx, ask, plan, header ? ts : null);
            Confirm(conn, ask, plan, header ? ts : null);
            return ApiResult.Ok(ArcCusInvoice.OkBody(ask, plan, true));
        }

        static ArcCusInvoicePlan Make(string op, ArcCusInvoiceAsk ask, List<ArcCusInvoiceRow> rows)
        {
            if (op == ArcCusInvoice.AddOp)
            {
                return ArcCusInvoicePlanner.MakeAdd(ask, rows);
            }
            if (op == ArcCusInvoice.DeleteOp)
            {
                return ArcCusInvoicePlanner.MakeDelete(ask, rows);
            }
            return ArcCusInvoicePlanner.MakeDefault(ask, rows);
        }

        // 开票单位必须是已有的客户；已停用（dEndDate）的允许。返回档案里的编码写法（U8 编码不分大小写，写库用档案的写法）。
        static string NeedCompany(object conn, string company)
        {
            Dictionary<string, object> row = Rows.One(conn, CompanySql, new object[] { company });
            if (row == null)
            {
                throw new BridgeException(409, "invoice_company_invalid", "开票单位不是已有的客户档案：" + company);
            }
            return ArcCustomerRow.Cell(row, "code");
        }

        // 可选的 expect.customer_ufts：客户当前 pubufts 必须一致，否则 409 state_mismatch（detail.current.customer_ufts 给当前值）。
        static void CheckTs(ArcCusInvoiceAsk ask, string ts)
        {
            if (ask.ExpTs == null || string.Equals(ask.ExpTs, ts, StringComparison.Ordinal))
            {
                return;
            }
            Dictionary<string, object> current = new Dictionary<string, object>();
            current["customer_ufts"] = ts;
            throw ArcCustomerRow.Mismatch("客户 " + ask.Customer + " 已被修改：customer_ufts 当前是 " + ts, current);
        }

        // headerTs 非空表示要改客户表头（值是读到的 pubufts）。
        static void Write(object conn, WorkContext ctx, ArcCusInvoiceAsk ask, ArcCusInvoicePlan plan, string headerTs)
        {
            foreach (ArcCusAddrStmt stmt in ArcCusInvoicePlanner.Updates(plan, ask.Customer))
            {
                GlSql.Exec(conn, stmt.Sql, stmt.Args);
            }
            if (plan.Insert != null)
            {
                ArcCusAddrStmt insert = ArcCusInvoicePlanner.Insert(plan, ask.Customer);
                GlSql.Exec(conn, insert.Sql, insert.Args);
            }
            if (plan.Remove != null)
            {
                ArcCusAddrStmt delete = ArcCusInvoicePlanner.Delete(plan, ask.Customer);
                GlSql.Exec(conn, delete.Sql, delete.Args);
            }
            if (headerTs != null)
            {
                ArcCustomerRow.Update(conn, ctx, "cInvoiceCompany=?", new object[] { plan.Header }, ask.Customer, headerTs);
            }
        }

        internal static List<ArcCusInvoiceRow> Load(object conn, string customer)
        {
            List<Dictionary<string, object>> raw = Rows.Query(conn, RowsSql, new object[] { customer }, RowsMax);
            if (raw.Count >= RowsMax)
            {
                throw new BridgeException(500, "internal", "客户 " + customer + " 的开票单位过多，无法处理");
            }
            List<ArcCusInvoiceRow> list = new List<ArcCusInvoiceRow>();
            foreach (Dictionary<string, object> r in raw)
            {
                ArcCusInvoiceRow row = new ArcCusInvoiceRow();
                long.TryParse(GlSql.Col(r, "id"), NumberStyles.Integer, CultureInfo.InvariantCulture, out row.Id);
                row.Code = GlSql.Col(r, "code");
                row.Default = GlSql.Int(r, "dflt") != 0;
                list.Add(row);
            }
            return list;
        }

        // 同一事务内重读整个客户的开票单位行，必须与计划的改后完全一致，否则 504（结果未知）；
        // 改了客户表头时，表头必须是新默认：pubufts 没变说明按 pubufts 的 UPDATE 没命中（别人改过，409），已变则结果未知（504）。
        static void Confirm(object conn, ArcCusInvoiceAsk ask, ArcCusInvoicePlan plan, string headerTs)
        {
            if (!ArcCusInvoicePlanner.Matches(plan.After, Load(conn, ask.Customer)))
            {
                throw ArcCustomerRow.Unknown(ask.Customer, "开票单位");
            }
            if (headerTs == null)
            {
                return;
            }
            Dictionary<string, object> row = ArcCustomerRow.Lock(conn, LockSql, ask.Customer);
            if (row == null)
            {
                throw ArcCustomerRow.Unknown(ask.Customer, "开票单位");
            }
            if (!ArcCustomerRow.Same(plan.Header, ArcCustomerRow.Cell(row, "header")))
            {
                throw ArcCustomerRow.Cell(row, "ts") == headerTs
                    ? ArcCustomerRow.Concurrent(ask.Customer) : ArcCustomerRow.Unknown(ask.Customer, "开票单位");
            }
        }
    }
}
