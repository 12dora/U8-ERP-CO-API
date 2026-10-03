using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 表头自定义项上的一个条件（vouchers/search 的 defines）。Op：eq 规整（Normalize）后相等，like 包含，prefix 开头是。
    // Pattern 是进参数的值：eq 为规整后的原文，like / prefix 为已转义的 LIKE 模式（同 code_like）。
    internal sealed class DefineMatch
    {
        public string Key;
        public int Number;
        public string Op;
        public string Pattern;
    }

    // vouchers/search 的 defines：按表头自定义项（cDefine1–16 中的文本列）找单据，例如业务员录在表头自定义项 1 的纸质合同号。
    // 键是 define1 … define16，只收文本列：表头表上 cDefine1–3、8–14 是 nvarchar，
    // 4、6 是 datetime，5、15 是 int，7、16 是 float / decimal（sys.columns 核对过），不能按文本比。
    // 列名只来自这里的常量（h.cDefine + 编号），调用方的值只进 ? 参数。
    internal static class VoucherSearchDefines
    {
        public const string Field = "defines";
        public const int MaxKeys = 4;
        public const int MaxLen = 120;
        const string Prefix = "define";
        const string Alias = "def_";

        static readonly int[] TextNumbers = new int[] { 1, 2, 3, 8, 9, 10, 11, 12, 13, 14 };
        static readonly string[] Ops = new string[] { "eq", "like", "prefix" };

        // 表头表有 cDefine1–16 的列表类型（sys.columns 核对，与各类型写白名单的表头 cdefine1–16 一致）。
        // 生产订单（mom_order 的列叫 Define1–16）、物料清单（bom_bom 没有）不支持。新类型缺省不支持。
        static readonly HashSet<string> Kinds = new HashSet<string>(StringComparer.Ordinal)
        {
            "sale_order", "dispatch", "sale_return", "sale_invoice",
            "purchase_order", "arrival", "purchase_return", "purchase_invoice", "purchase_requisition",
            "purchase_in", "other_in", "other_out", "product_in", "material_out", "sale_out",
            "transfer", "transfer_request", "shape_change", "stock_check",
            // 货位调整单：AdjustPVouch 有 cDefine1–16。
            "position_adjust",
            "qm_incoming_inspect", "qm_product_inspect", "qm_incoming_check", "qm_product_check",
            "qm_incoming_reject", "qm_product_reject", "qm_other_inspect", "qm_other_check",
            "ar_receipt", "ap_payment", "ar_bill", "ap_bill",
            // 供应商退款、客户退款：同表 Ap_CloseBill。
            "ap_refund", "ar_refund",
            // 出入库调整单：JustInVouch 的 cDefine1–16 同上（数据字典核对）。
            IaAdjustRead.KindName,
            // 退货申请单：SA_ReturnsApplyMain 的 cDefine1–16 同上（sys.columns 核对）。
            ReturnsApplyRead.KindName
        };

        public static bool Supports(ListKind kind)
        {
            return kind != null && Kinds.Contains(kind[ListKind.Name]);
        }

        // 缺省或 null 返回 null。
        public static List<DefineMatch> Parse(Dictionary<string, object> body, ListKind kind)
        {
            object raw = Requests.Field(body, Field);
            if (raw == null)
            {
                return null;
            }
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null || map.Count < 1 || map.Count > MaxKeys)
            {
                throw BridgeException.BadField(Field, "defines 必须是 1 到 "
                    + MaxKeys.ToString(CultureInfo.InvariantCulture) + " 个键的对象").WithHint(Hint());
            }
            List<DefineMatch> list = new List<DefineMatch>(map.Count);
            foreach (KeyValuePair<string, object> pair in map)
            {
                DefineMatch match = new DefineMatch();
                match.Key = pair.Key ?? "";
                match.Number = NumberOf(match.Key);
                match.Key = KeyOf(match.Number);
                if (!Supports(kind))
                {
                    throw BridgeException.BadField(Field, "该单据类型没有表头自定义项 " + match.Key);
                }
                ParseSpec(match, pair.Value);
                list.Add(match);
            }
            return list;
        }

        // define1 … define16 中的文本列。只收规范写法（键恰好等于 "define" + 编号）：不收前导零（define01）、
        // 空白、结尾 \0 等（.NET Framework 的 int.TryParse 会接受结尾的 \0）。
        static int NumberOf(string key)
        {
            int n = 0;
            string tail = key.StartsWith(Prefix, StringComparison.Ordinal) ? key.Substring(Prefix.Length) : "";
            int.TryParse(tail, NumberStyles.None, CultureInfo.InvariantCulture, out n);
            if (n < 1 || n > 16 || key != KeyOf(n))
            {
                throw BridgeException.BadField(Safe(key) ? FieldPath.Join(Field, key) : Field,
                    "未知的表头自定义项 " + (Safe(key) ? key : "（键含不允许的字符）")).WithHint(Hint());
            }
            if (Array.IndexOf(TextNumbers, n) < 0)
            {
                throw BridgeException.BadField(FieldPath.Join(Field, key), "表头自定义项 " + key
                    + " 是日期或数字列，不能按文本搜索").WithHint(Hint());
            }
            return n;
        }

        // 规范键 defineN；SQL 别名和响应键都由编号生成，不用请求里的原文。
        static string KeyOf(int number)
        {
            return Prefix + number.ToString(CultureInfo.InvariantCulture);
        }

        // 能放进 field 路径和消息的键：1 到 40 个 ASCII 字母、数字、下划线。
        static bool Safe(string key)
        {
            if (key.Length == 0 || key.Length > 40)
            {
                return false;
            }
            for (int i = 0; i < key.Length; i++)
            {
                char c = key[i];
                if (c > 127 || !(char.IsLetterOrDigit(c) || c == '_'))
                {
                    return false;
                }
            }
            return true;
        }

        // 字符串等同 {"eq": …}；对象恰好一个键 eq / like / prefix。值规整（Normalize）后 1 到 120 个字符、不含控制字符。
        static void ParseSpec(DefineMatch match, object raw)
        {
            string at = FieldPath.Join(Field, match.Key);
            object value = raw;
            match.Op = "eq";
            Dictionary<string, object> spec = raw as Dictionary<string, object>;
            if (spec != null)
            {
                if (spec.Count != 1)
                {
                    throw BridgeException.BadField(at, match.Key + " 必须是字符串或 {eq | like | prefix: 字符串}");
                }
                foreach (KeyValuePair<string, object> pair in spec)
                {
                    match.Op = pair.Key;
                    value = pair.Value;
                }
                if (Array.IndexOf(Ops, match.Op) < 0)
                {
                    throw BridgeException.BadField(at, match.Key + " 只收 eq、like、prefix 之一");
                }
            }
            string text = Text(value, at);
            match.Pattern = match.Op == "eq" ? text : ArcRead.Like(text, match.Op == "like");
        }

        static string Text(object value, string at)
        {
            string text = value as string;
            text = text == null ? null : Normalize(text);
            if (text == null || text.Length == 0 || text.Length > MaxLen || HasControl(text))
            {
                throw BridgeException.BadField(at, "自定义项的值必须是 1 到 "
                    + MaxLen.ToString(CultureInfo.InvariantCulture)
                    + " 个字符的字符串（全角空格、不换行空格当半角空格，去掉两端空格后）");
            }
            return text;
        }

        // 与 SQL 一侧（Column）同一套规整：全角空格 U+3000、不换行空格 U+00A0 换成半角空格，再去两端半角空格。
        // LTRIM / RTRIM 只去半角空格（TRIM(字符 FROM …) 要 SQL Server 2017），所以两边都只去半角空格，其他空白字符两边都原样保留。
        internal static string Normalize(string text)
        {
            return text.Replace('\u3000', ' ').Replace('\u00A0', ' ').Trim(' ');
        }

        static bool HasControl(string text)
        {
            for (int i = 0; i < text.Length; i++)
            {
                if (char.IsControl(text[i]))
                {
                    return true;
                }
            }
            return false;
        }

        static string Hint()
        {
            StringBuilder sb = new StringBuilder("可用的键（表头文本自定义项）：");
            for (int i = 0; i < TextNumbers.Length; i++)
            {
                sb.Append(i == 0 ? "" : "、").Append(Prefix).Append(TextNumbers[i].ToString(CultureInfo.InvariantCulture));
            }
            return sb.ToString();
        }

        // 列值规整同 Normalize：全角空格、不换行空格先换成半角空格（NCHAR 常量，不占参数），再 LTRIM / RTRIM。
        // 中间的全角空格也会变成半角空格，请求值同样处理，所以相等、包含的比较两边一致。
        static string Column(DefineMatch match)
        {
            return "LTRIM(RTRIM(REPLACE(REPLACE(h.cDefine" + match.Number.ToString(CultureInfo.InvariantCulture)
                + ", NCHAR(12288), N' '), NCHAR(160), N' ')))";
        }

        // SELECT 里另加各条件列（去空格，空串为 NULL），别名 def_defineN。
        public static void AppendSelect(StringBuilder sb, List<DefineMatch> list)
        {
            if (list == null)
            {
                return;
            }
            foreach (DefineMatch match in list)
            {
                sb.Append(", NULLIF(").Append(Column(match)).Append(", N'') AS ")
                    .Append(Alias).Append(KeyOf(match.Number));
            }
        }

        // WHERE 条件，与其他条件 AND。
        public static void AppendWhere(StringBuilder sb, List<DefineMatch> list, List<object> ps)
        {
            if (list == null)
            {
                return;
            }
            foreach (DefineMatch match in list)
            {
                sb.Append(" AND ").Append(Column(match));
                sb.Append(match.Op == "eq" ? " = ?" : " LIKE ? ESCAPE '\\'");
                ps.Add(match.Pattern);
            }
        }

        // 每条另加 defines：{"define1": 值或 null}，只含请求里的键。items 与 rows 按下标一一对应（ListRoutes.Page）。
        public static void Attach(Dictionary<string, object> result, List<Dictionary<string, object>> rows,
            List<DefineMatch> list)
        {
            List<object> items = list == null ? null : result["items"] as List<object>;
            if (items == null)
            {
                return;
            }
            for (int i = 0; i < items.Count; i++)
            {
                Dictionary<string, object> item = (Dictionary<string, object>)items[i];
                Dictionary<string, object> values = new Dictionary<string, object>(list.Count);
                foreach (DefineMatch match in list)
                {
                    object raw;
                    rows[i].TryGetValue(Alias + KeyOf(match.Number), out raw);
                    string text = raw as string;
                    text = text == null ? null : Normalize(text);
                    values[KeyOf(match.Number)] = string.IsNullOrEmpty(text) ? null : text;
                }
                item[Field] = values;
            }
        }
    }
}
