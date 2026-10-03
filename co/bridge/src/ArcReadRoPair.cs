using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 两列主键的只读档案：编码写成 "<第一列>:<第二列>"，按第一个冒号拆开（第一列的值不含冒号），两段各按表列长度校验。
    // SQL 只用这两列加 ? 参数，不拼接调用方的文本。列表按 (第一列, 第二列) 排序，after 拆开后按同一顺序比较；
    // code_prefix 按整串 "<第一列>:<第二列>" 匹配。class_code 是第一列。
    // Obj1 / Obj2：对应段的记录级数据权限对象（与 PermRegistry 的登记一致），get 按段判断，list 由 PermHook.Where 加条件。
    internal sealed class ArcPair
    {
        public string Col1;
        public string Col2;
        public int Max1;
        public int Max2;
        public string Format;
        public string Obj1;
        public string Obj2;
        // 第二段可以为空（"<第一列>:"）：客户、供应商联系人新增时编码由 U8 自动编号（ArcPartnerContact、ArcVenContact），
        // 其余操作由调用方再拒绝空段。
        public bool OpenSecond;

        // 列长度取自 U8 表定义（nvarchar 的字符数）。
        static readonly Dictionary<string, ArcPair> Specs = Build();

        static Dictionary<string, ArcPair> Build()
        {
            Dictionary<string, ArcPair> map = new Dictionary<string, ArcPair>(StringComparer.Ordinal);
            // 客户收货地址 CusDeliverAdd：主键 (cCusCode, cAddCode)。
            ArcPair address = Spec("cCusCode", 20, "cAddCode", 30, "<客户编码>:<地址编码>");
            address.Obj1 = PermObj.Customer;
            map.Add("customer_address", address);
            // 自定义项档案 UserDefine：主键 (cID, cValue)，cID 是自定义项号。
            map.Add("user_define", Spec("cID", 10, "cValue", 400, "<自定义项号>:<档案值>"));
            // 客户存货对照 CusInvContrapose：U8 的唯一索引是 (cCusCode, cInvCode, cCusInvCode)，同一客户同一存货
            // 理论上可以有多条（客户存货编码不同）；桥按 (cCusCode, cInvCode) 两列作编码。
            ArcPair contra = Spec("cCusCode", 20, "cInvCode", 60, "<客户编码>:<存货编码>");
            contra.Obj1 = PermObj.Customer;
            contra.Obj2 = PermObj.Inventory;
            map.Add("customer_inventory", contra);
            // 客户、供应商的银行账户（主键 (cCusCode / cVenCode, cAccountNum)，账号 nvarchar(50)）和联系人
            // （cContactCode nvarchar(30)），见 ArcPartner。
            Partner(map, "customer_bank", true, "cAccountNum", 50, "<客户编码>:<银行账号>");
            Partner(map, "vendor_bank", false, "cAccountNum", 50, "<供应商编码>:<银行账号>");
            Partner(map, "customer_contact", true, "cContactCode", 30, "<客户编码>:<联系人编码>");
            map["customer_contact"].OpenSecond = true;
            Partner(map, "vendor_contact", false, "cContactCode", 30, "<供应商编码>:<联系人编码>");
            map["vendor_contact"].OpenSecond = true;
            return map;
        }

        static void Partner(Dictionary<string, ArcPair> map, string name, bool customer, string col2, int max2, string format)
        {
            ArcPair p = Spec(customer ? "cCusCode" : "cVenCode", 20, col2, max2, format);
            p.Obj1 = customer ? PermObj.Customer : PermObj.Vendor;
            map.Add(name, p);
        }

        static ArcPair Spec(string col1, int max1, string col2, int max2, string format)
        {
            ArcPair p = new ArcPair();
            p.Col1 = col1;
            p.Max1 = max1;
            p.Col2 = col2;
            p.Max2 = max2;
            p.Format = format;
            return p;
        }

        internal static ArcPair Of(ArcKind kind)
        {
            ArcPair p;
            if (kind == null || !Specs.TryGetValue(kind.Name, out p))
            {
                return null;
            }
            return p;
        }

        // ArcKindRo 建档案时调用：主键、分类列取第一列，编码整串最长 Max1 + 1 + Max2。
        internal static ArcKind Apply(ArcKind k)
        {
            ArcPair p = Specs[k.Name];
            k.Key = p.Col1;
            k.ClassCol = p.Col1;
            k.CodeMax = p.Max1 + 1 + p.Max2;
            return k;
        }

        // "<第一列>:<第二列>" → { 第一列, 第二列 }；两段都要 1 到各自长度个字符、前后没有空格。
        internal string[] Split(string code, string label)
        {
            int at = code == null ? -1 : code.IndexOf(':');
            if (at < 1 || (at == code.Length - 1 && !OpenSecond))
            {
                throw ArcReq.Bad(label + " 必须写成 " + Format, FieldPath.Clean(label));
            }
            string first = code.Substring(0, at);
            string second = code.Substring(at + 1);
            if (!Part(first, Max1) || (second.Length > 0 && !Part(second, Max2)))
            {
                throw ArcReq.Bad(label + " 必须写成 " + Format + "，两段长度分别不超过 "
                    + Max1.ToString(System.Globalization.CultureInfo.InvariantCulture) + " 和 "
                    + Max2.ToString(System.Globalization.CultureInfo.InvariantCulture) + "，前后不能有空格", FieldPath.Clean(label));
            }
            return new string[] { first, second };
        }

        // 按编码定位一行的条件和参数（ArcRead.Row / Exists，可写档案的新增、修改、删除与回读）：两列主键按两段，其余按 Key。
        internal static string KeyWhere(ArcKind kind)
        {
            ArcPair p = Of(kind);
            return p == null ? kind.Key + "=?" : p.Col1 + "=? AND " + p.Col2 + "=?";
        }

        internal static object[] KeyArgs(ArcKind kind, string code)
        {
            ArcPair p = Of(kind);
            if (p == null)
            {
                return new object[] { code };
            }
            string[] parts = p.Split(code, "code");
            return new object[] { parts[0], parts[1] };
        }

        static bool Part(string text, int max)
        {
            return text.Length > 0 && text.Length <= max && text.Trim().Length == text.Length;
        }

        string CodeExpr()
        {
            return "(h." + Col1 + " + ':' + h." + Col2 + ")";
        }

        public static ApiResult Get(WorkContext ctx, ArcReq req)
        {
            ArcKind k = req.Kind;
            ArcPair p = Of(k);
            string[] parts = p.Split(req.Code, "code");
            StringBuilder sql = new StringBuilder("SELECT h.*");
            if (k.Ts != null)
            {
                sql.Append(", CONVERT(varchar(20), CONVERT(bigint, h.").Append(k.Ts).Append(")) AS ").Append(ArcReadRo.UftsAlias);
            }
            sql.Append(" FROM ").Append(k.Table).Append(" h WHERE h.").Append(p.Col1).Append("=? AND h.").Append(p.Col2).Append("=?");
            Dictionary<string, object> row = Rows.One(ctx.Conn, sql.ToString(), new object[] { parts[0], parts[1] });
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "档案不存在");
            }
            p.Permit(ctx, parts);
            Dictionary<string, object> body = ArcRead.Head(req);
            body["fields"] = ArcReadRo.Fields(row, k.Ts);
            if (k.Ts != null)
            {
                body["ufts"] = ArcRead.Cell(row, ArcReadRo.UftsAlias);
            }
            return ApiResult.Ok(body);
        }

        // 单条读取的数据权限：客户段、存货段各按自己的对象判断，与列表的 PermHook.Where 条件一致。
        void Permit(WorkContext ctx, string[] parts)
        {
            if ((Obj1 != null && !PermHook.Allows(ctx, Obj1, parts[0]))
                || (Obj2 != null && !PermHook.Allows(ctx, Obj2, parts[1])))
            {
                throw new BridgeException(403, "no_permission", PermCheck.DeniedRow);
            }
        }

        public static ApiResult List(WorkContext ctx, ArcReq req)
        {
            ArcKind k = req.Kind;
            ArcPair p = Of(k);
            object conn = ctx.Conn;
            string watermark = k.Ts == null ? null : Rows.Scalar(conn, ArcRead.WatermarkSql, null);
            List<object> args = new List<object>();
            args.Add(req.Limit + 1);
            StringBuilder sql = new StringBuilder("SELECT TOP (?) ").Append(p.CodeExpr()).Append(" AS code, h.");
            sql.Append(k.NameCol).Append(" AS name, h.").Append(p.Col1).Append(" AS class_code");
            if (k.Ts != null)
            {
                sql.Append(", CONVERT(varchar(20), CONVERT(bigint, h.").Append(k.Ts).Append(")) AS ufts");
            }
            sql.Append(" FROM ").Append(k.Table).Append(" h WHERE 1=1");
            p.AddAfter(sql, args, req.After);
            ArcRead.AddFilter(sql, args, " AND " + p.CodeExpr() + " LIKE ? ESCAPE '\\'", ArcRead.Like(req.Prefix, false));
            ArcRead.AddFilter(sql, args, " AND h." + k.NameCol + " LIKE ? ESCAPE '\\'", ArcRead.Like(req.NameLike, true));
            if (k.Ts != null)
            {
                ArcRead.AddFilter(sql, args, " AND h." + k.Ts + " > CONVERT(binary(8), CONVERT(bigint, ?))", req.Since);
            }
            // 数据权限：客户收货地址按客户，客户存货对照按客户和存货。
            PermHook.Where(sql, args, ctx, "h");
            sql.Append(" ORDER BY h.").Append(p.Col1).Append(", h.").Append(p.Col2);
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql.ToString(), args.ToArray(), req.Limit + 1);
            return ApiResult.Ok(ArcReadRo.Page(req, rows, watermark));
        }

        // after 拆成两段，与 ORDER BY (第一列, 第二列) 同序：第一列更大，或第一列相同而第二列更大。
        void AddAfter(StringBuilder sql, List<object> args, string after)
        {
            if (string.IsNullOrEmpty(after))
            {
                return;
            }
            string[] parts = Split(after, "after");
            sql.Append(" AND (h.").Append(Col1).Append(" > ? OR (h.").Append(Col1).Append(" = ? AND h.").Append(Col2).Append(" > ?))");
            args.Add(parts[0]);
            args.Add(parts[0]);
            args.Add(parts[1]);
        }
    }
}
