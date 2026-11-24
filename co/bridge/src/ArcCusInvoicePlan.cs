using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 客户开票单位的一行（sa_invoicecustomers）。表只有自增列 autoid 作主键，(客户, 开票单位) 是逻辑键。
    internal sealed class ArcCusInvoiceRow
    {
        public long Id;
        public string Code = "";
        public bool Default;

        public ArcCusInvoiceRow Copy()
        {
            ArcCusInvoiceRow row = new ArcCusInvoiceRow();
            row.Id = Id;
            row.Code = Code;
            row.Default = Default;
            return row;
        }

        public Dictionary<string, object> Map()
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["code"] = Code;
            map["bdefault"] = Default;
            return map;
        }
    }

    // 一次新增、删除或设默认的计划：整个客户的改前、改后各行（Before / After 下标一一对应，新增的行排在 After 最后、Id 为 0）。
    // Insert 非空表示要插一行（它是 After 的最后一行）；Remove 非空表示要删一行（它不在 After 里）；
    // Dirty 是只改默认标志的已有行；Header 非空表示默认开票单位变了，Customer.cInvoiceCompany 要改成它。
    internal sealed class ArcCusInvoicePlan
    {
        public List<ArcCusInvoiceRow> Before = new List<ArcCusInvoiceRow>();
        public List<ArcCusInvoiceRow> After = new List<ArcCusInvoiceRow>();
        public List<int> Dirty = new List<int>();
        public ArcCusInvoiceRow Insert;
        public ArcCusInvoiceRow Remove;
        public string Header;

        public bool Changed
        {
            get { return Insert != null || Remove != null || Dirty.Count > 0; }
        }
    }

    // 纯函数：新增判重 / 幂等重放、删除与设默认的 expect 和默认行校验、生成 SQL、回读核对。不连库，--selftest 直接测。
    internal static class ArcCusInvoicePlanner
    {
        const string InsertSql = "INSERT INTO sa_invoicecustomers (ccuscode, cinvoicecompany, bdefault) VALUES (?, ?, ?)";
        const string UpdateSql = "UPDATE sa_invoicecustomers SET bdefault=? WHERE autoid=CONVERT(bigint, ?) AND ccuscode=?";
        const string DeleteSql = "DELETE FROM sa_invoicecustomers WHERE autoid=CONVERT(bigint, ?) AND ccuscode=?";

        // 新增。同一对已存在：要求默认而它不是默认 → 409 invoice_company_taken（改默认用 customer_invoice_company），
        // 否则视为重放（不写）。新行在客户还没有默认行时（含第一行）或请求 default=true 时成为唯一默认。
        public static ArcCusInvoicePlan MakeAdd(ArcCusInvoiceAsk ask, List<ArcCusInvoiceRow> rows)
        {
            ArcCusInvoicePlan plan = Start(rows);
            int at = Find(rows, ask.Company);
            if (at >= 0)
            {
                if (ask.Default && !rows[at].Default)
                {
                    throw new BridgeException(409, "invoice_company_taken", "开票单位 " + rows[at].Code
                        + " 已是该客户的开票单位但不是默认；要改默认请用 customer_invoice_company");
                }
                return plan;
            }
            ArcCusInvoiceRow row = new ArcCusInvoiceRow();
            row.Code = ask.Company;
            row.Default = ask.Default || DefaultAt(rows) < 0;
            ClearDefaults(plan, row.Default ? -1 : DefaultAt(rows));
            plan.Insert = row;
            plan.After.Add(row);
            return Finish(plan);
        }

        // 删除。不存在 404 invoice_company_not_found；expect 不符 409 state_mismatch；默认行 409
        // invoice_company_is_default（客户有了开票单位就至少留一行，且必有默认）。删的一定是非默认行，
        // 剩下各行的默认标志和默认开票单位都不变，所以没有 Dirty 和 Header（After 比 Before 少一行，不走 Finish）。
        public static ArcCusInvoicePlan MakeDelete(ArcCusInvoiceAsk ask, List<ArcCusInvoiceRow> rows)
        {
            int at = Locate(ask, rows);
            if (rows[at].Default)
            {
                throw new BridgeException(409, "invoice_company_is_default", "默认开票单位不能删除，请先设置其他默认");
            }
            ArcCusInvoicePlan plan = Start(rows);
            plan.Remove = rows[at];
            plan.After.RemoveAt(at);
            return plan;
        }

        // 设默认。这一行为 1、其余为 0；已是唯一默认则不写。
        public static ArcCusInvoicePlan MakeDefault(ArcCusInvoiceAsk ask, List<ArcCusInvoiceRow> rows)
        {
            int at = Locate(ask, rows);
            ArcCusInvoicePlan plan = Start(rows);
            ClearDefaults(plan, at);
            plan.After[at].Default = true;
            return Finish(plan);
        }

        static ArcCusInvoicePlan Start(List<ArcCusInvoiceRow> rows)
        {
            ArcCusInvoicePlan plan = new ArcCusInvoicePlan();
            foreach (ArcCusInvoiceRow row in rows)
            {
                plan.Before.Add(row);
                plan.After.Add(row.Copy());
            }
            return plan;
        }

        // 算出哪些已有行要改默认标志，以及默认开票单位有没有换：改后的默认与改前不同才要改客户表头；
        // 改前不是恰好一个默认（没有或多个，数据已不一致）时表头无从判断，一律按改后的默认写（表头已一致则不写）。
        static ArcCusInvoicePlan Finish(ArcCusInvoicePlan plan)
        {
            for (int i = 0; i < plan.Before.Count; i++)
            {
                if (plan.Before[i].Default != plan.After[i].Default)
                {
                    plan.Dirty.Add(i);
                }
            }
            int was = DefaultAt(plan.Before);
            int now = DefaultAt(plan.After);
            bool single = was >= 0 && DefaultCount(plan.Before) == 1;
            if (now >= 0 && (!single || !ArcCusAddrPlanner.CodeEq(plan.Before[was].Code, plan.After[now].Code)))
            {
                plan.Header = plan.After[now].Code;
            }
            return plan;
        }

        // keep 之外的默认行全部取消（含数据已不一致、多行默认的情况）。
        static void ClearDefaults(ArcCusInvoicePlan plan, int keep)
        {
            for (int i = 0; i < plan.After.Count; i++)
            {
                if (i != keep)
                {
                    plan.After[i].Default = false;
                }
            }
        }

        static int DefaultCount(List<ArcCusInvoiceRow> rows)
        {
            int count = 0;
            foreach (ArcCusInvoiceRow row in rows)
            {
                if (row.Default)
                {
                    count++;
                }
            }
            return count;
        }

        static int DefaultAt(List<ArcCusInvoiceRow> rows)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                if (rows[i].Default)
                {
                    return i;
                }
            }
            return -1;
        }

        static int Find(List<ArcCusInvoiceRow> rows, string company)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                if (ArcCusAddrPlanner.CodeEq(rows[i].Code, company))
                {
                    return i;
                }
            }
            return -1;
        }

        // 删除、设默认共用：定位这一对并核对 expect.bdefault。
        static int Locate(ArcCusInvoiceAsk ask, List<ArcCusInvoiceRow> rows)
        {
            int at = Find(rows, ask.Company);
            if (at < 0)
            {
                throw new BridgeException(404, "invoice_company_not_found", "客户 " + ask.Customer + " 没有开票单位 " + ask.Company);
            }
            ArcCusInvoiceRow target = rows[at];
            if (ask.ExpDefault != target.Default)
            {
                Dictionary<string, object> current = new Dictionary<string, object>();
                current["bdefault"] = target.Default;
                throw ArcCustomerRow.Mismatch("开票单位 " + target.Code + " 与预期不符：默认当前是「"
                    + (target.Default ? "是" : "否") + "」", current);
            }
            return at;
        }

        // autoid 是 bigint，桥的参数只收 int / string / null：以十进制串绑定，SQL 里 CONVERT(bigint, ?)。
        static string IdText(ArcCusInvoiceRow row)
        {
            return row.Id.ToString(CultureInfo.InvariantCulture);
        }

        // 取消默认的行逐条 UPDATE，再把新默认设上（Dirty 按下标顺序，取消与设上互不影响）。
        internal static List<ArcCusAddrStmt> Updates(ArcCusInvoicePlan plan, string customer)
        {
            List<ArcCusAddrStmt> list = new List<ArcCusAddrStmt>();
            foreach (int i in plan.Dirty)
            {
                ArcCusAddrStmt stmt = new ArcCusAddrStmt();
                stmt.Sql = UpdateSql;
                stmt.Args = new object[] { plan.After[i].Default ? 1 : 0, IdText(plan.After[i]), customer };
                list.Add(stmt);
            }
            return list;
        }

        internal static ArcCusAddrStmt Insert(ArcCusInvoicePlan plan, string customer)
        {
            ArcCusAddrStmt stmt = new ArcCusAddrStmt();
            stmt.Sql = InsertSql;
            stmt.Args = new object[] { customer, plan.Insert.Code, plan.Insert.Default ? 1 : 0 };
            return stmt;
        }

        internal static ArcCusAddrStmt Delete(ArcCusInvoicePlan plan, string customer)
        {
            ArcCusAddrStmt stmt = new ArcCusAddrStmt();
            stmt.Sql = DeleteSql;
            stmt.Args = new object[] { IdText(plan.Remove), customer };
            return stmt;
        }

        // 写后回读核对：行数一致；已有行按 Id 对上，新行 Id 未知按开票单位编码找且 Id 大于 0；编码、默认标志都一致。
        internal static bool Matches(List<ArcCusInvoiceRow> expected, List<ArcCusInvoiceRow> actual)
        {
            if (expected.Count != actual.Count)
            {
                return false;
            }
            foreach (ArcCusInvoiceRow want in expected)
            {
                ArcCusInvoiceRow got = want.Id == 0 ? ByCode(actual, want.Code) : ById(actual, want.Id);
                if (got == null || got.Id <= 0 || want.Default != got.Default
                    || !ArcCusAddrPlanner.CodeEq(want.Code, got.Code))
                {
                    return false;
                }
            }
            return true;
        }

        static ArcCusInvoiceRow ByCode(List<ArcCusInvoiceRow> rows, string code)
        {
            int at = Find(rows, code);
            return at < 0 ? null : rows[at];
        }

        static ArcCusInvoiceRow ById(List<ArcCusInvoiceRow> rows, long id)
        {
            foreach (ArcCusInvoiceRow row in rows)
            {
                if (row.Id == id)
                {
                    return row;
                }
            }
            return null;
        }

        internal static List<object> Maps(List<ArcCusInvoiceRow> rows)
        {
            List<object> list = new List<object>();
            foreach (ArcCusInvoiceRow row in rows)
            {
                list.Add(row.Map());
            }
            return list;
        }
    }
}
