using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // vouchers/search 的请求。登录前（Requests.ApplyP4）和读线程上（VoucherSearch.Run）各解析一次，规则相同。
    // 部门、业务员、仓库、制单人、日期、已审核、已关闭换成 vouchers/list 的筛选键，交给 ListSql.AppendFilters；
    // code_like、partner、inventory、defines 是本路由另加的条件（VoucherSearchSql、VoucherSearchDefines）。
    internal sealed class VoucherSearchArgs
    {
        public const int DefaultLimit = 50;
        public const int MaxLimit = 200;
        const int CodeLikeMax = 40;

        // 请求字段 → 列表筛选键 → ListKind 列（-1 表示按 FlagSql / 日期列，另行判断）。
        static readonly string[] TextFields = new string[] { "dept", "person", "warehouse", "maker" };
        static readonly string[] TextKeys = new string[] { "dep_code", "person_code", "wh_code", "maker" };
        static readonly int[] TextCols = new int[] { ListKind.Dep, ListKind.Person, ListKind.Wh, ListKind.Maker };

        public ListKind Kind;
        public int After;
        public int Limit = DefaultLimit;
        // 按 ListSql.AppendFilters 的键存放。
        public readonly Dictionary<string, object> Filters = new Dictionary<string, object>(StringComparer.Ordinal);
        // 已转成 LIKE 模式（含两端 %、已转义）；null 表示不按单号筛选。
        public string CodeLike;
        public string Partner;
        public string Inventory;
        // 表头自定义项条件（VoucherSearchDefines）；null 表示没给 defines。
        public List<DefineMatch> Defines;

        public static VoucherSearchArgs Parse(Dictionary<string, object> body)
        {
            if (body == null)
            {
                throw new BridgeException(400, "bad_request", "请求体为空");
            }
            VoucherSearchArgs args = new VoucherSearchArgs();
            args.Kind = KindOf(body);
            args.After = OptInt(body, "after", 0, 0, int.MaxValue);
            args.Limit = OptInt(body, "limit", DefaultLimit, 1, MaxLimit);
            args.ParseText(body);
            args.ParseDates(body);
            args.ParseFlags(body);
            args.ParseExtra(body);
            return args;
        }

        static ListKind KindOf(Dictionary<string, object> body)
        {
            string name = Requests.Field(body, "type") as string;
            if (name == null)
            {
                throw BridgeException.BadField("type", "type 必须是字符串");
            }
            ListKind kind = ListKinds.Find(name);
            if (kind == null)
            {
                throw BridgeException.BadField("type", "不支持搜索的单据类型 " + ListArgs.Clip(name))
                    .WithHint("可用的单据类型见 /v1/co/meta");
            }
            return kind;
        }

        void ParseText(Dictionary<string, object> body)
        {
            for (int i = 0; i < TextFields.Length; i++)
            {
                string value = Text(body, TextFields[i], 60);
                if (value == null)
                {
                    continue;
                }
                Require(Kind.Has(TextCols[i]), TextFields[i]);
                Filters[TextKeys[i]] = value;
            }
        }

        void ParseDates(Dictionary<string, object> body)
        {
            string[] keys = new string[] { "date_from", "date_to" };
            for (int i = 0; i < keys.Length; i++)
            {
                string value = Text(body, keys[i], 10);
                if (value != null)
                {
                    Filters[keys[i]] = Guard(delegate { return ListSql.CheckDate(value, keys[i]); }, keys[i]);
                }
            }
        }

        void ParseFlags(Dictionary<string, object> body)
        {
            object verified = Guard(delegate { return Flag(body, "verified"); }, "verified");
            if (verified != null)
            {
                Filters["verified"] = verified;
            }
            object closed = Guard(delegate { return Flag(body, "closed"); }, "closed");
            if (closed != null)
            {
                Require(Kind.ClosedSql() != null, "closed");
                Filters["closed"] = closed;
            }
        }

        void ParseExtra(Dictionary<string, object> body)
        {
            string code = Text(body, "code_like", CodeLikeMax);
            CodeLike = ArcRead.Like(code, true);
            Partner = Text(body, "partner", 60);
            if (Partner != null)
            {
                Require(Kind.Has(ListKind.Cus) || Kind.Has(ListKind.Ven), "partner");
            }
            Inventory = Text(body, "inventory", 60);
            if (Inventory != null)
            {
                Require(VoucherSearchSql.HasInventory(Kind), "inventory");
            }
            Defines = VoucherSearchDefines.Parse(body, Kind);
        }

        static void Require(bool present, string field)
        {
            if (!present)
            {
                throw BridgeException.BadField(field, "该单据类型不支持按 " + field + " 搜索");
            }
        }

        static object Flag(Dictionary<string, object> body, string key)
        {
            object raw = Requests.Field(body, key);
            if (raw == null)
            {
                return null;
            }
            return ListArgs.OptBool(body, key, key);
        }

        // 1 到 max 个字符、不含控制字符；缺省或 null 返回 null。
        static string Text(Dictionary<string, object> body, string key, int max)
        {
            object raw = Requests.Field(body, key);
            if (raw == null)
            {
                return null;
            }
            string text = raw as string;
            if (text == null || text.Length == 0 || text.Length > max || HasControl(text))
            {
                throw BridgeException.BadField(key, key + " 必须是 1 到 "
                    + max.ToString(CultureInfo.InvariantCulture) + " 个字符的字符串");
            }
            return text;
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

        static int OptInt(Dictionary<string, object> body, string key, int fallback, int min, int max)
        {
            object raw = Requests.Field(body, key);
            if (raw == null)
            {
                return fallback;
            }
            long number = raw is int ? (int)raw : raw is long ? (long)raw : long.MinValue;
            if (number < min || number > max)
            {
                throw BridgeException.BadField(key, key + " 必须是 " + min.ToString(CultureInfo.InvariantCulture) + " 到 "
                    + max.ToString(CultureInfo.InvariantCulture) + " 的整数");
            }
            return (int)number;
        }

        // 复用的校验函数抛的 400 没有 field，补上请求字段名。
        static object Guard(Func<object> check, string field)
        {
            try
            {
                return check();
            }
            catch (BridgeException ex)
            {
                if (ex.Status == 400 && string.IsNullOrEmpty(ex.Field))
                {
                    ex.WithField(field);
                }
                throw;
            }
        }
    }
}
