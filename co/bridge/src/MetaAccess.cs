using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;

namespace U8Co
{
    // meta 路由读取各领域类的私有白名单：只在这里开口，逻辑仍由原类的判断函数决定，避免两份清单漂移。
    internal static partial class StockDom
    {
        internal static bool MetaAllowed(VoucherKind kind, bool head, string name)
        {
            return Allowed(kind, head, name);
        }

        internal static IEnumerable<string> MetaNames()
        {
            List<string> all = new List<string>();
            all.AddRange(HeadNames);
            all.AddRange(SaleOutHeadNames);
            all.AddRange(BodyNames);
            all.AddRange(TransferHeadNames);
            all.AddRange(TransferBodyNames);
            // 无来源采购入库单。
            all.AddRange(PurMetaNames());
            // 形态转换单、调拨申请单、盘点单。
            all.AddRange(MiscMetaNames());
            // 货位调整单。
            all.AddRange(AdjustMetaNames());
            return all;
        }
    }

    internal static partial class SaleGen
    {
        // 发货单参照销售订单：表体可带自由项。
        internal static bool MetaDispatch(string key, bool head)
        {
            return Allowed(key, head, head ? HeadKeys : LineKeys, !head);
        }

        // 销售发票参照发货单：表体不带自由项。
        internal static bool MetaInvoice(string key, bool head)
        {
            return Allowed(key, head, head ? InvHeadKeys : InvLineKeys, false);
        }

        internal static string[] MetaNames()
        {
            return (HeadKeys + "," + LineKeys + "," + InvHeadKeys + "," + InvLineKeys).Split(',');
        }
    }

    internal static partial class StockGen
    {
        internal static bool MetaHeadKey(string low)
        {
            return HeadKey(low);
        }

        internal static bool MetaLineKey(string low)
        {
            return LineKey(low);
        }
    }

    internal static partial class PuArr
    {
        internal static string[] MetaHeadKeys()
        {
            return (string[])HeadKeys.Clone();
        }
    }

    internal static partial class Requests
    {
        const string LoginPath = "/u8co/v1/login-check";

        // 每条路由及其请求体允许的顶层字段（含公共字段）。路径取自 Dispatch 的分派表（加上在 StaWorker 里直接处理的
        // login-check），字段取自请求校验同一张字段表（ExtraKeys）。查不到字段表的路径 keys 为 null，表示两张表漂移了。
        internal static List<KeyValuePair<string, string[]>> MetaRoutes()
        {
            SortedSet<string> paths = new SortedSet<string>(MetaDispatchPaths(), StringComparer.Ordinal);
            paths.Add(LoginPath);
            List<KeyValuePair<string, string[]>> list = new List<KeyValuePair<string, string[]>>();
            foreach (string path in paths)
            {
                list.Add(new KeyValuePair<string, string[]>(path, MetaKeys(path)));
            }
            return list;
        }

        static string[] MetaKeys(string path)
        {
            if (path == LoginPath)
            {
                return (string[])CommonKeys.Clone();
            }
            if (path == "/u8co/v1/sale-orders/verify" || path == "/u8co/v1/dispatches/verify")
            {
                return (string[])VoucherKeys.Clone();
            }
            string[] extra = ExtraKeys(path);
            return extra == null ? null : JoinKeys(extra);
        }

        // Dispatch.Routes 是私有静态表；这里按字段名反射读取键，不改 Dispatch.cs。字段改名时返回空，meta 的 routes 只剩 login-check。
        static IEnumerable<string> MetaDispatchPaths()
        {
            List<string> paths = new List<string>();
            FieldInfo field = typeof(Dispatch).GetField("Routes", BindingFlags.NonPublic | BindingFlags.Static);
            IDictionary map = field == null ? null : field.GetValue(null) as IDictionary;
            if (map == null)
            {
                return paths;
            }
            foreach (object key in map.Keys)
            {
                string path = key as string;
                if (path != null)
                {
                    paths.Add(path);
                }
            }
            return paths;
        }
    }
}
