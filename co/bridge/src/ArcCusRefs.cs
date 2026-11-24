using System;
using System.Collections.Generic;

namespace U8Co
{
    // customer_fields 里引用其他档案的列：存在、末级、未停用、协议用途（iLZYJ）、对应供应商唯一，以及随本列联动的列
    // （所属行业带出行业名称）。查询只拼声明表里的表名列名；值走参数。
    internal static class ArcCusRefs
    {
        const string HeadKey = "head_corp_code";
        const string VendorKey = "crelvendor";
        // 销售默认协议：销售模块已启用时 iLZYJ 取 0 或 1，否则只取 1（与 U8 CheckRef 一致）。
        const string SaFilter = "(iLZYJ=0 OR iLZYJ=1)";
        const string SaSub = "SA";
        const string InvoiceSql = "SELECT LTRIM(RTRIM(ISNULL(cInvoiceCompany,''))) AS v FROM Customer WHERE cCusCode=?";
        const string VendorTakenSql = "SELECT TOP 1 LTRIM(RTRIM(cCusCode)) AS code FROM Customer"
            + " WHERE cRelVendor=? AND cCusCode<>?";
        const string VendorClearSql = "UPDATE Vendor SET cRelCustomer=NULL WHERE cRelCustomer=?";
        const string VendorLinkSql = "UPDATE Vendor SET cRelCustomer=? WHERE cVenCode=?";

        // 引用档案的查询：编码（库里存的写法）、停用日期、联动值；where 是档案上的固定条件（末级标志、协议用途）。
        internal static string RefSql(ArcCusCol col, string where)
        {
            string sql = "SELECT LTRIM(RTRIM(" + col.RefCode + ")) AS code";
            if (col.RefStop != null)
            {
                sql += ", ISNULL(CONVERT(varchar(10), " + col.RefStop + ", 23),'') AS ended";
            }
            if (col.PairFrom != null)
            {
                sql += ", LTRIM(RTRIM(ISNULL(" + col.PairFrom + ",''))) AS pair";
            }
            sql += " FROM " + col.RefTable + " WHERE " + col.RefCode + "=?";
            return where == null ? sql : sql + " AND " + where;
        }

        // 请求的引用对应的写入值：空串清空（总公司留空是客户自己）；否则必须在档案里（末级 / 用途 / 未停用），返回档案里存的编码，
        // 并记下联动列要写的值。
        public static string Stored(object conn, WorkContext ctx, ArcCusFieldsAsk ask, ArcCusCol col, string wanted)
        {
            bool head = col.Special == ArcCusCol.SpHead;
            if (wanted.Length == 0)
            {
                Pair(ask, col, "");
                return head ? ask.Self : "";
            }
            if (head && string.Equals(wanted, ask.Self, StringComparison.OrdinalIgnoreCase))
            {
                return ask.Self;
            }
            Dictionary<string, object> row = Rows.One(conn, RefSql(col, WhereOf(ctx, col)), new object[] { wanted });
            if (row == null)
            {
                throw Invalid(col, Missing(col) + wanted);
            }
            if (col.RefStop != null && Stopped(ArcCustomerRow.Cell(row, "ended"), ctx.Item.Date))
            {
                throw Invalid(col, col.Label + "已停用：" + wanted);
            }
            string code = ArcCustomerRow.Cell(row, "code");
            Pair(ask, col, col.PairFrom == null ? "" : ArcCustomerRow.Cell(row, "pair"));
            if (col.Special == ArcCusCol.SpVendor)
            {
                CheckVendorFree(conn, ask, code);
            }
            return code;
        }

        static void Pair(ArcCusFieldsAsk ask, ArcCusCol col, string value)
        {
            if (col.PairColumn != null)
            {
                ask.Pairs[col.Key] = value;
            }
        }

        static string WhereOf(WorkContext ctx, ArcCusCol col)
        {
            if (col.Special == ArcCusCol.SpSa && PeriodModules.StartedSubs(ctx).Contains(SaSub))
            {
                return SaFilter;
            }
            return col.RefWhere;
        }

        internal static string Missing(ArcCusCol col)
        {
            if (col.EndLevel)
            {
                return col.Label + "不存在或不是末级：";
            }
            return col.RefWhere != null ? col.Label + "不存在或不可用于该字段：" : col.Label + "不存在：";
        }

        internal static BridgeException Invalid(ArcCusCol col, string message)
        {
            return new BridgeException(409, col.Key + "_invalid", message, FieldPath.Join("fields", col.Key));
        }

        // 停用日期非空且不晚于登录日期即为已停用（与业务员 dPInValidDate 同口径）；登录日期未知时不拦。
        internal static bool Stopped(string ended, string date)
        {
            return date != null && date.Length > 0 && ended != null && ended.Length > 0
                && string.CompareOrdinal(ended, date) <= 0;
        }

        // 一个供应商只能对应一个客户（U8 CheckExist）。
        static void CheckVendorFree(object conn, ArcCusFieldsAsk ask, string vendor)
        {
            if (Rows.One(conn, VendorTakenSql, new object[] { vendor, ask.Code }) != null)
            {
                throw new BridgeException(409, VendorKey + "_taken", "供应商已对应其他客户：" + vendor,
                    FieldPath.Join("fields", VendorKey));
            }
        }

        // 改总公司时开票单位要么是客户自己、要么是新的总公司（U8 CheckNull 会把其他值改回客户自己；这里不改开票单位，直接拦）。
        public static void CheckHeadInvoice(object conn, ArcCusFieldsAsk ask, Dictionary<string, string> cur)
        {
            string head;
            if (!ask.Fields.TryGetValue(HeadKey, out head)
                || string.Equals(head, cur[HeadKey], StringComparison.OrdinalIgnoreCase))
            {
                return;
            }
            string invoice = (Rows.Scalar(conn, InvoiceSql, new object[] { ask.Code }) ?? "").Trim();
            if (invoice.Length == 0 || string.Equals(invoice, ask.Self, StringComparison.OrdinalIgnoreCase)
                || string.Equals(invoice, head, StringComparison.OrdinalIgnoreCase))
            {
                return;
            }
            throw new BridgeException(409, "head_corp_invoice_company",
                "开票单位 " + invoice + " 既不是本客户也不是新的所属总公司，请先调整开票单位", FieldPath.Join("fields", HeadKey));
        }

        // 对应供应商变了：像 U8 一样让供应商档案的反向关联（Vendor.cRelCustomer）跟着走——先清掉本客户原来关联的供应商，再指向新的。
        public static void SyncVendor(object conn, ArcCusFieldsAsk ask, Dictionary<string, string> cur)
        {
            string vendor;
            if (!ask.Fields.TryGetValue(VendorKey, out vendor)
                || string.Equals(vendor, cur[VendorKey], StringComparison.Ordinal))
            {
                return;
            }
            GlSql.Exec(conn, VendorClearSql, new object[] { ask.Self });
            if (vendor.Length > 0)
            {
                GlSql.Exec(conn, VendorLinkSql, new object[] { ask.Self, vendor });
            }
        }
    }
}
