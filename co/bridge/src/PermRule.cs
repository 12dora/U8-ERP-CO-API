using System;
using System.Collections.Generic;

namespace U8Co
{
    // 一条读路由（或路由 + 单据类型 / 档案 / 报表）要的功能权限和要过滤的数据权限对象。
    internal sealed class PermRule
    {
        public string Key;
        // 403 文案「没有<Title>权限」里的功能名。
        public string Title;
        // 可接受的 cAuth_Id，任一即可；空数组表示登录成功即可。以 % 结尾表示前缀。
        public string[] Auths;
        public PermObj[] Objs;

        public PermRule(string key, string title, string[] auths, PermObj[] objs)
        {
            Key = key;
            Title = title;
            Auths = auths ?? new string[0];
            Objs = objs ?? new PermObj[0];
        }
    }

    // 一个受控对象落在哪一列。列名只来自本仓库的常量表（PermRegistry），不来自请求。
    internal sealed class PermObj
    {
        public const string Customer = "customer";
        public const string Vendor = "vendor";
        public const string Department = "department";
        public const string Person = "person";
        public const string HrPerson = "hr_hi_person";
        public const string Warehouse = "warehouse";
        public const string Inventory = "inventory";
        public const string Account = "code";
        public const string Item = "fitem";
        public const string SaleType = "saletype";
        public const string PurchaseType = "purchasetype";
        public const string RdStyle = "rd_style";
        public const string Sign = "dsign";

        // Head：表头（或报表行）上的一列；Body：表体行，单据保留条件是「表体为空或有一行放行」；
        // Pair：项目大类 + 项目编码两列；GlLines：总账凭证，按凭证键找分录上的科目。
        public const int Head = 0;
        public const int Body = 1;
        public const int Pair = 2;
        public const int GlLines = 3;

        // 读取会用到的对象。user（制单人）只管删改他人单据，gzauth（工资）没有路由，position（货位）不展开。
        static readonly HashSet<string> Used = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            Customer, Vendor, Department, Person, Warehouse, Inventory, Account, Item, SaleType, PurchaseType,
            RdStyle, Sign
        };

        public string Obj;
        public int Mode;
        // Head / Body：编码列；Pair：项目编码列。
        public string Column;
        // Pair：项目大类列。
        public string ClassColumn;
        // Body：表体表、表体外键列、表头主键列。
        public string BodyTable;
        public string BodyFk;
        public string HeadKey;
        // 该列在这类单据上本来就可以为空（例如采购入库单上的客户）：空值不算越权。其余列空值一律不放行。
        public bool Optional;

        public static PermObj H(string obj, string column)
        {
            PermObj o = new PermObj();
            o.Obj = obj;
            o.Mode = Head;
            o.Column = column;
            return o;
        }

        public static PermObj Opt(string obj, string column)
        {
            PermObj o = H(obj, column);
            o.Optional = true;
            return o;
        }

        public static PermObj B(string obj, string table, string fk, string headKey, string column)
        {
            PermObj o = H(obj, column);
            o.Mode = Body;
            o.BodyTable = table;
            o.BodyFk = fk;
            o.HeadKey = headKey;
            return o;
        }

        public static PermObj BOpt(string obj, string table, string fk, string headKey, string column)
        {
            PermObj o = B(obj, table, fk, headKey, column);
            o.Optional = true;
            return o;
        }

        public static PermObj P(string classColumn, string codeColumn)
        {
            PermObj o = H(Item, codeColumn);
            o.Mode = Pair;
            o.ClassColumn = classColumn;
            return o;
        }

        public static PermObj Gl()
        {
            PermObj o = H(Account, "ccode");
            o.Mode = GlLines;
            o.BodyTable = "GL_accvouch";
            return o;
        }

        public static string Normalize(string obj)
        {
            return obj == null ? "" : obj.Trim().ToLowerInvariant();
        }

        // 读取会用到的全部对象（PermSnapshot 按它逐个输出），按序号序排好的新数组。
        internal static string[] ReadObjects()
        {
            List<string> list = new List<string>(Used);
            list.Sort(StringComparer.Ordinal);
            return list.ToArray();
        }

        public static bool NotForRead(string obj)
        {
            return !Used.Contains(obj ?? "");
        }
    }
}
