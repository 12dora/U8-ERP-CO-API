using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 客户自定义项（self_define1…16 → Customer.cCusDefine1…16）按账套的客户自定义项设置校验（UserDef_Base，cClass=客户，
    // cDicDbName=列名），次序与 U8 一致：必输项不能清空 → 使用长度（定长时必须等长）和小数位数 → 要求合法性检查（bCheckVal=1）的项，
    // 值必须在自定义项档案（UserDefine）里；取自其他档案（iDataSource=1 或设置了关联档案）的项本路由不处理。
    // 自动建档（bArchive）只对 U8 界面录入生效，本路由不往自定义项档案里加值。列类型（文本 / 整数 / 小数 / 日期）与格式在解析时已按声明表校验。
    // 自定义项的含义由调用方决定，桥不作假设。
    internal static class ArcCusDefine
    {
        internal const string SettingSql = "SELECT cID AS id, CONVERT(int, ISNULL(iLength,0)) AS len,"
            + " CONVERT(int, ISNULL(iMaxLen,0)) AS maxlen, CONVERT(int, ISNULL(bFixLength,0)) AS fix,"
            + " CONVERT(int, ISNULL(bCheckVal,0)) AS chk, CONVERT(int, ISNULL(iDataSource,0)) AS src,"
            + " ISNULL(cRelArchive,N'') AS rel, CONVERT(int, ISNULL(bInput,0)) AS req,"
            + " ISNULL(CONVERT(int, iDecimalDigits),-1) AS digits"
            + " FROM UserDef_Base WHERE cClass=N'客户' AND cDicDbName=?";
        internal const string ArchiveSql = "SELECT cValue AS v FROM UserDefine WHERE cID=? AND cValue=?";

        // 请求值对应的写入值：空串清空（必输项不行）；否则按设置校验，返回写入的写法（档案里存的值）。
        public static string Check(object conn, ArcCusCol col, string wanted)
        {
            Dictionary<string, object> def = Rows.One(conn, SettingSql, new object[] { col.Column });
            if (def == null)
            {
                if (wanted.Length == 0)
                {
                    return wanted;
                }
                throw ArcCusRefs.Invalid(col, col.Label + "没有设置：" + col.Column);
            }
            if (wanted.Length == 0)
            {
                if (Num(def, "req") == 1)
                {
                    string path = FieldPath.Join("fields", col.Key);
                    throw ArcReq.Bad(path + " 是必输项，不能为空", path);
                }
                return wanted;
            }
            CheckSize(col, new ArcCusDefineRule(Num(def, "len"), Num(def, "maxlen"), Num(def, "fix") == 1,
                Num(def, "digits")), wanted);
            return Num(def, "chk") == 1 ? Archived(conn, col, def, wanted) : wanted;
        }

        static string Archived(object conn, ArcCusCol col, Dictionary<string, object> def, string wanted)
        {
            if (Num(def, "src") == 1 || ArcCustomerRow.Cell(def, "rel").Length > 0)
            {
                throw ArcCusRefs.Invalid(col, col.Label + "的值取自其他档案，本路由不支持修改");
            }
            Dictionary<string, object> row = Rows.One(conn, ArchiveSql,
                new object[] { ArcCustomerRow.Cell(def, "id"), wanted });
            if (row == null)
            {
                throw ArcCusRefs.Invalid(col, col.Label + "的值不在自定义项档案里：" + wanted);
            }
            return ArcCustomerRow.Cell(row, "v");
        }

        // 文本不超过使用长度（取使用长度、设置的最大长度、列宽中最小的正数），定长时必须等长；小数不超过设置的小数位数。
        internal static void CheckSize(ArcCusCol col, ArcCusDefineRule rule, string wanted)
        {
            string path = FieldPath.Join("fields", col.Key);
            if (col.Kind == ArcCusCol.KText)
            {
                int cap = Cap(Cap(col.Max, rule.Len), rule.MaxLen);
                if (wanted.Length > cap)
                {
                    throw ArcReq.Bad(path + " 长度不能超过自定义项的使用长度 " + cap.ToString(CultureInfo.InvariantCulture), path);
                }
                if (rule.Fix && rule.Len > 0 && wanted.Length != rule.Len)
                {
                    throw ArcReq.Bad(path + " 是定长 " + rule.Len.ToString(CultureInfo.InvariantCulture) + " 个字符", path);
                }
                return;
            }
            if (col.Kind == ArcCusCol.KNum && rule.Digits >= 0 && ArcCusValue.Fraction(wanted) > rule.Digits)
            {
                throw ArcReq.Bad(path + " 最多 " + rule.Digits.ToString(CultureInfo.InvariantCulture) + " 位小数", path);
            }
        }

        // 两个上限里较小的正数（不为正的表示没有这个上限）。
        internal static int Cap(int a, int b)
        {
            if (b <= 0)
            {
                return a;
            }
            return a <= 0 || b < a ? b : a;
        }

        static int Num(Dictionary<string, object> row, string name)
        {
            int value;
            return int.TryParse(ArcCustomerRow.Cell(row, name), NumberStyles.Integer, CultureInfo.InvariantCulture, out value)
                ? value : 0;
        }
    }

    // 自定义项设置里与取值大小有关的四项：使用长度、设置的最大长度、是否定长、小数位数（负数表示不限）。
    internal sealed class ArcCusDefineRule
    {
        public readonly int Len;
        public readonly int MaxLen;
        public readonly bool Fix;
        public readonly int Digits;

        public ArcCusDefineRule(int len, int maxLen, bool fix, int digits)
        {
            Len = len;
            MaxLen = maxLen;
            Fix = fix;
            Digits = digits;
        }
    }
}
