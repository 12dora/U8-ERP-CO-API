using System;
using System.Collections.Generic;

namespace U8Co
{
    // archives/<op> 的请求：登录前解析、校验（Parse），编码、字段、列表过滤条件。
    internal sealed class ArcReq
    {
        const int MaxCode = 30;
        const int MaxValue = 2000;
        const int MaxFields = 300;

        public string Op;
        public ArcKind Kind;
        public ArcMap Map;
        public string Code;
        public string Template;
        public ArcBag Fields = new ArcBag();
        public string Prefix;
        public string NameLike;
        public string Since;
        public string After;
        public int Limit = 100;
        // 项目列表只列这一个项目大类（fitem.citem_class）。
        public string ProjectClass;
        // 汇率列表（ArcExch）：只列这一币种；Year 为 0 时取登录年度。
        public string Currency;
        public int Year;
        // 固定资产卡片列表（ArcFa）：类别编码前缀、使用部门、是否含已减少的卡片。
        public string TypeCode;
        public string DeptCode;
        public bool IncludeDisposed;
        // 列表只返回 code、ufts（ArcListExtra，删除扫描用）。
        public bool KeysOnly;

        public static ArcReq Parse(string op, Dictionary<string, object> body)
        {
            ArcReq req = new ArcReq();
            req.Op = NeedOp(op);
            req.Kind = KindOf(body, req.Op);
            if (req.Op == "list")
            {
                req.ParseList(body);
                return req;
            }
            req.Code = req.KeyText(Text(body, "code"), "code");
            req.Map = ArcMap.Of(req.Kind);
            if (req.Op == "create" || req.Op == "update")
            {
                req.ParseFields(Field(body, "fields"));
            }
            if (req.Op == "create")
            {
                req.ParseCreate(body);
            }
            else if (req.Op == "update" && req.Fields.Count == 0)
            {
                throw Bad("没有要修改的字段", "fields");
            }
            KindChecks(req);
            return req;
        }

        // 按档案类型的登录前校验：项目（ArcProjectWrite）、币种和凭证类别（ArcGlKinds）、汇率（ArcExchWrite）、原因码（ArcReason）、
        // 收付款协议、自定义项档案和客户存货对照的修改、存货的 Inventory_Sub 列（ArcKindWrites）。
        static void KindChecks(ArcReq req)
        {
            if (ArcKindRo.IsProject(req.Kind) && req.Op != "get")
            {
                ArcProjectWrite.Check(req);
            }
            if (ArcGlKinds.Owns(req.Kind))
            {
                ArcGlKinds.Check(req);
            }
            if (ArcExch.Is(req.Kind))
            {
                ArcExchWrite.Check(req);
            }
            if (ArcReason.Is(req.Kind))
            {
                ArcReason.Check(req);
            }
            ArcKindWrites.Check(req);
            // 固定资产卡片新增（FaCardReq）、设备台账新增（ArcEq）。
            FaWrites.Check(req);
        }

        // 只读档案（ArcKind.ReadOnly）只允许 get、list。
        static ArcKind KindOf(Dictionary<string, object> body, string op)
        {
            string archive = Text(body, "archive");
            ArcKind kind = ArcKind.Find(archive);
            if (kind == null)
            {
                throw Bad(archive == null ? "缺少 archive" : "未知档案类型 " + archive, "archive").WithHint("可用的档案类型见 /v1/co/meta");
            }
            if (kind.ReadOnly && !ArcRoutes.IsRead(op))
            {
                throw Bad("该档案只读");
            }
            if (kind.NoDelete && op == "delete")
            {
                throw Bad("档案 " + kind.Name + " 不支持删除，请在 U8 客户端处理");
            }
            if (kind.NoUpdate && op == "update")
            {
                throw Bad(kind.NoUpdateText ?? ("档案 " + kind.Name + " U8 不提供修改，请删除后重新新增"));
            }
            return kind;
        }

        static string NeedOp(string op)
        {
            if (op == "get" || op == "list" || op == "create" || op == "update" || op == "delete")
            {
                return op;
            }
            throw Bad("未知操作 " + (op ?? ""), "op");
        }

        void ParseCreate(Dictionary<string, object> body)
        {
            string template = Text(body, "template");
            if (template != null)
            {
                Template = CheckCode(template, "template");
            }
            if (Kind.NeedTemplate && Template == null)
            {
                throw Bad("新增存货必须提供 template（参照的存货编码）", "template");
            }
            // 两列主键的档案（自定义项档案、客户存货对照）不收模板。
            if (Template != null && Kind.CodeTags != null)
            {
                throw Bad("档案 " + Kind.Name + " 不支持 template", "template");
            }
            if (Kind.NameTag != null && !Fields.Has(Kind.NameTag))
            {
                throw Bad("缺少字段 " + Kind.NameTag, FieldPath.Join("fields", Kind.NameTag));
            }
        }

        void ParseFields(object raw)
        {
            if (raw == null)
            {
                throw Bad("缺少 fields", "fields");
            }
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw Bad("fields 必须是对象", "fields");
            }
            if (map.Count > MaxFields)
            {
                throw Bad("字段太多", "fields");
            }
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (KeyValuePair<string, object> pair in map)
            {
                string tag = Map.Canon(pair.Key);
                if (tag == null)
                {
                    throw Bad("未知字段 " + pair.Key, FieldPath.Join("fields", pair.Key)).WithHint(FieldPath.WritableHint);
                }
                if (Kind.Blocked(tag))
                {
                    throw Bad("不能设置字段 " + tag, FieldPath.Join("fields", pair.Key));
                }
                // 同一列的两个别名标签（如 subscribe_point / SubscribePoint）也算重复。
                if (!seen.Add(Map.Column(tag)))
                {
                    throw Bad("字段重复 " + pair.Key, FieldPath.Join("fields", pair.Key));
                }
                Fields.Put(tag, ValueText(tag, pair.Value));
            }
        }

        void ParseList(Dictionary<string, object> body)
        {
            // 前缀最长与该档案的编码一样（项目是 "<大类>:<编码>" 整串，最长 63）。
            Prefix = Plain(Text(body, "code_prefix"), "code_prefix", Kind.CodeMax);
            NameLike = Plain(Text(body, "name_like"), "name_like", 60);
            string after = Text(body, "after");
            if (after != null)
            {
                After = KeyText(after, "after");
            }
            Since = Rowversion(Text(body, "changed_since"));
            ParseListRo(body);
            ParseExch(body);
            ArcFa.ParseList(this, Text(body, "type_code"), Text(body, "dept_code"), Field(body, "include_disposed"));
            KeysOnly = ArcListExtra.ParseKeysOnly(Field(body, "keys_only"));
            object limit = Field(body, "limit");
            if (limit == null)
            {
                return;
            }
            if (!(limit is int) || (int)limit < 1 || (int)limit > 500)
            {
                throw Bad("limit 必须是 1 到 500 的整数", "limit");
            }
            Limit = (int)limit;
        }

        // 只读档案的列表条件：没有 rowversion 的表拒绝 changed_since；project_class 只给项目用。
        void ParseListRo(Dictionary<string, object> body)
        {
            if (Since != null && Kind.Ts == null)
            {
                throw Bad("档案 " + Kind.Name + " 没有 ufts，不支持 changed_since", "changed_since");
            }
            string cls = Text(body, "project_class");
            if (cls == null)
            {
                return;
            }
            if (!ArcKindRo.IsProject(Kind))
            {
                throw Bad("只有 project 支持 project_class", "project_class");
            }
            ProjectClass = ArcProject.CheckClass(cls);
        }

        // currency、fiscal_year 只给 exchange_rate（year 是登录用的账套库年度，这里不用）；after 不能是 get 专用的 "<币种>:<日期>"。
        void ParseExch(Dictionary<string, object> body)
        {
            string currency = Text(body, "currency");
            object year = Field(body, "fiscal_year");
            if (!ArcExch.Is(Kind))
            {
                if (currency != null || year != null)
                {
                    throw Bad("只有 exchange_rate 支持 currency、fiscal_year", currency != null ? "currency" : "fiscal_year");
                }
                return;
            }
            Currency = currency == null ? null : ExchKey.CurrencyText(currency, "currency");
            Year = ExchKey.YearOf(year);
            ExchKey.CheckAfter(After);
        }

        // 编码按档案的最大长度校验；项目编码还要是 "<项目大类>:<项目编码>"，两列主键的档案要是 ArcPair 的格式。
        string KeyText(string text, string label)
        {
            string code = CheckCode(text, label, Kind.CodeMax);
            if (ArcKindRo.IsProject(Kind))
            {
                ArcProject.Split(code, label);
            }
            ArcPair pair = ArcPair.Of(Kind);
            if (pair != null)
            {
                pair.Split(code, label);
            }
            if (ArcExch.Is(Kind))
            {
                ExchKey.Parse(code, label);
            }
            return code;
        }

        static string Rowversion(string text)
        {
            if (text == null)
            {
                return null;
            }
            long value;
            bool digits = text.Length > 0 && text.Length <= 19;
            for (int i = 0; digits && i < text.Length; i++)
            {
                digits = text[i] >= '0' && text[i] <= '9';
            }
            if (!digits || !long.TryParse(text, out value))
            {
                throw Bad("changed_since 必须是十进制 ufts 字符串", "changed_since");
            }
            return value.ToString(System.Globalization.CultureInfo.InvariantCulture);
        }

        // 编码 1–30 个字符，不含控制字符，前后没有空格。
        internal static string CheckCode(string text, string label)
        {
            return CheckCode(text, label, MaxCode);
        }

        internal static string CheckCode(string text, string label, int max)
        {
            if (text == null || text.Length == 0)
            {
                throw Bad("缺少 " + label, FieldPath.Clean(label));
            }
            if (text.Length > max)
            {
                throw Bad(label + " 长度必须在 1 到 " + max.ToString(System.Globalization.CultureInfo.InvariantCulture) + " 之间", FieldPath.Clean(label));
            }
            if (text.Trim().Length != text.Length)
            {
                throw Bad(label + " 前后不能有空格", FieldPath.Clean(label));
            }
            return Plain(text, label, max);
        }

        static string Plain(string text, string label, int max)
        {
            if (text == null)
            {
                return null;
            }
            if (text.Length > max)
            {
                throw Bad(label + " 太长", FieldPath.Clean(label));
            }
            if (HasControl(text, false))
            {
                throw Bad(label + " 含有控制字符", FieldPath.Clean(label));
            }
            return text;
        }

        static string ValueText(string tag, object value)
        {
            if (value == null)
            {
                return null;
            }
            string text = value as string;
            if (text != null)
            {
                if (text.Length > MaxValue || HasControl(text, true))
                {
                    throw Bad("字段 " + tag + " 的值太长或含有控制字符", FieldPath.Join("fields", tag));
                }
                return text;
            }
            if (value is bool)
            {
                return (bool)value ? "1" : "0";
            }
            return NumberText(tag, value);
        }

        static string NumberText(string tag, object value)
        {
            IFormattable number = value as IFormattable;
            bool numeric = value is int || value is long || value is decimal;
            if (value is double)
            {
                double d = (double)value;
                numeric = !double.IsNaN(d) && !double.IsInfinity(d);
            }
            if (number == null || !numeric)
            {
                throw Bad("字段 " + tag + " 的值必须是字符串、数字或布尔", FieldPath.Join("fields", tag));
            }
            return number.ToString(value is double ? "R" : null, System.Globalization.CultureInfo.InvariantCulture);
        }

        static bool HasControl(string text, bool allowSpace)
        {
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (allowSpace && (c == '\t' || c == '\r' || c == '\n'))
                {
                    continue;
                }
                if (c < ' ' || c == '\x7f' || c == '\uFFFE' || c == '\uFFFF')
                {
                    return true;
                }
            }
            return false;
        }

        static object Field(Dictionary<string, object> body, string key)
        {
            object value;
            if (body == null || !body.TryGetValue(key, out value))
            {
                return null;
            }
            return value;
        }

        static string Text(Dictionary<string, object> body, string key)
        {
            object value = Field(body, key);
            if (value == null)
            {
                return null;
            }
            string text = value as string;
            if (text == null)
            {
                throw Bad(key + " 必须是字符串", key);
            }
            return text;
        }

        internal static BridgeException Bad(string message)
        {
            return new BridgeException(400, "bad_request", message);
        }

        internal static BridgeException Bad(string message, string field)
        {
            return new BridgeException(400, "bad_request", message, field);
        }
    }
}
