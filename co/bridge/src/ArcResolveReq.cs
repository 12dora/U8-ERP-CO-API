using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // archives/resolve 的一项：档案类型 + 去掉两端空格后的查询文本。
    internal sealed class ResolveItem
    {
        public ArcKind Kind;
        public string Q;
    }

    // archives/resolve 的请求：items 1–20 项，limit 1–20（缺省 5），include_disabled 缺省 false。
    // 登录前（Requests.ApplyP4 → ArcRoutes.Check）和处理函数里各解析一次，都是纯校验，不查库。
    internal sealed class ArcResolveReq
    {
        internal const int MaxItems = 20;
        internal const int MaxLimit = 20;
        internal const int MaxQ = 100;
        const string KindHint = "两段编码的档案（收货地址、自定义项、客户存货对照、银行账户、联系人）、exchange_rate、fa_card、equipment 不支持解析，请用 archives/list";

        // 不支持解析的档案：两列主键 / 挂在上级档案下的档案、汇率、固定资产卡片。
        static readonly HashSet<string> Excluded = new HashSet<string>(StringComparer.Ordinal)
        {
            "customer_address", "customer_inventory", "user_define", "customer_bank", "vendor_bank",
            "customer_contact", "vendor_contact", ArcExch.Name, ArcFa.Name,
            // 设备台账（ArcEq）：与 API 一致，不做名称解析。
            ArcEq.Name
        };

        public readonly List<ResolveItem> Items = new List<ResolveItem>();
        public int Limit = 5;
        public bool IncludeDisabled;

        internal static bool Supports(ArcKind kind)
        {
            return kind != null && !Excluded.Contains(kind.Name) && ArcPair.Of(kind) == null && !ArcPartner.Is(kind);
        }

        public static ArcResolveReq Parse(Dictionary<string, object> body)
        {
            ArcResolveReq req = new ArcResolveReq();
            IList items = ItemList(body);
            for (int i = 0; i < items.Count; i++)
            {
                req.Items.Add(ParseItem(items[i], FieldPath.Item("items", i)));
            }
            req.Limit = ParseLimit(Field(body, "limit"));
            object off = Field(body, "include_disabled");
            if (off != null && !(off is bool))
            {
                throw BridgeException.BadField("include_disabled", "include_disabled 必须是布尔值");
            }
            req.IncludeDisabled = off != null && (bool)off;
            return req;
        }

        static IList ItemList(Dictionary<string, object> body)
        {
            object raw = Field(body, "items");
            if (raw == null)
            {
                throw BridgeException.BadField("items", "缺少字段 items");
            }
            IList items = raw as IList;
            if (items == null || raw is string)
            {
                throw BridgeException.BadField("items", "items 必须是 JSON 数组");
            }
            if (items.Count < 1 || items.Count > MaxItems)
            {
                throw BridgeException.BadField("items", "items 必须是 1 到 20 项");
            }
            return items;
        }

        static ResolveItem ParseItem(object raw, string at)
        {
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw BridgeException.BadField(at, "items 的每一项必须是对象");
            }
            foreach (string key in map.Keys)
            {
                if (key != "archive" && key != "q")
                {
                    throw BridgeException.BadField(FieldPath.Join(at, key), "含未知字段 " + key);
                }
            }
            ResolveItem item = new ResolveItem();
            item.Kind = KindOf(Field(map, "archive"), FieldPath.Join(at, "archive"));
            item.Q = QueryText(Field(map, "q"), FieldPath.Join(at, "q"));
            return item;
        }

        static ArcKind KindOf(object raw, string at)
        {
            string name = raw as string;
            if (name == null)
            {
                throw BridgeException.BadField(at, raw == null ? "缺少 archive" : "archive 必须是字符串");
            }
            ArcKind kind = ArcKind.Find(name);
            if (kind == null)
            {
                throw BridgeException.BadField(at, "未知档案类型 " + name);
            }
            if (!Supports(kind))
            {
                throw new BridgeException(400, "bad_request", "档案 " + name + " 不支持解析", at, KindHint);
            }
            return kind;
        }

        // 去掉两端空格后 1 到 100 个字符，不含控制字符。
        static string QueryText(object raw, string at)
        {
            string text = raw as string;
            if (text == null)
            {
                throw BridgeException.BadField(at, raw == null ? "缺少 q" : "q 必须是字符串");
            }
            string q = text.Trim();
            if (q.Length < 1 || q.Length > MaxQ)
            {
                throw BridgeException.BadField(at, "q 去掉两端空格后必须是 1 到 100 个字符");
            }
            for (int i = 0; i < q.Length; i++)
            {
                if (char.IsControl(q[i]) || q[i] == '￾' || q[i] == '￿')
                {
                    throw BridgeException.BadField(at, "q 含有控制字符");
                }
            }
            return q;
        }

        static int ParseLimit(object raw)
        {
            if (raw == null)
            {
                return 5;
            }
            if (!(raw is int) || (int)raw < 1 || (int)raw > MaxLimit)
            {
                throw BridgeException.BadField("limit", "limit 必须是 1 到 20 的整数");
            }
            return (int)raw;
        }

        static object Field(Dictionary<string, object> map, string key)
        {
            object value;
            if (map == null || !map.TryGetValue(key, out value))
            {
                return null;
            }
            return value;
        }
    }
}
