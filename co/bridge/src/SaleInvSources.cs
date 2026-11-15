using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 销售发票参照发货单生单的一张来源：发货单 DLID 和要开的发货行（同 vouchers/generate 的 lines 形状）。
    internal sealed class InvSource
    {
        public int Id;
        public object[] Lines;
    }

    // vouchers/generate type=sale_invoice 的多来源形状：sources = [{id: DLID, lines: [...]}, ...]，与单张的 id + lines 互斥。
    // 单张的 id + lines 是 n = 1 的同一条路径（SaleGen.Invoice 把它包成一个来源）。只收参照发货单（source_type 省略或 dispatch）；
    // 来源 1 到 200 张、DLID 不重复，各行合计 1 到 200 行，行形状同 Json.GenerateLines。审计、幂等和失败时的单据 id 取第一张来源。
    // 每张来源发货单都锁 "dispatch:<DLID>"，另锁 "new:sale_invoice"（DocLocks.GenerateKeys）。
    internal static class SaleInvSources
    {
        internal const string Field = "sources";
        const string Path = "/u8co/v1/vouchers/generate";
        const int MaxLines = 200;

        internal static bool Asked(Dictionary<string, object> body, string path)
        {
            return path == Path && body != null && body.ContainsKey(Field);
        }

        // 登录前（Requests.ApplyId）：解析 sources，id 取第一张来源。
        internal static void Apply(Dictionary<string, object> body, WorkItem item, AuditDraft draft)
        {
            if (item.Type == null || item.Type.Name != "sale_invoice")
            {
                throw BridgeException.BadField(Field, "只有销售发票参照发货单可以给 sources");
            }
            if (body.ContainsKey("id") || body.ContainsKey("lines"))
            {
                throw BridgeException.BadField(Field, "sources 不能与 id、lines 同时给出");
            }
            item.GenSources = Parse(body[Field], item.Type);
            item.HasId = true;
            item.Id = item.GenSources[0].Id;
            if (draft != null)
            {
                draft.HasId = true;
                draft.Id = item.Id;
            }
        }

        // 来源类型已按 Kinds.Sources 定下（Requests.FillGenerate）：多来源只给参照发货单。
        internal static void CheckSource(WorkItem item)
        {
            if (item.GenSources != null && (item.Source == null || item.Source.Name != "dispatch"))
            {
                throw BridgeException.BadField(Field, "sources 只用于参照发货单生成销售发票");
            }
        }

        // 纯解析（--selftest 用）。
        internal static InvSource[] Parse(object raw, VoucherKind kind)
        {
            IList list = raw as IList;
            if (list == null)
            {
                throw BridgeException.BadField(Field, "sources 必须是 JSON 数组");
            }
            if (list.Count < 1 || list.Count > MaxLines)
            {
                throw BridgeException.BadField(Field, "sources 必须是 1 到 200 张发货单");
            }
            InvSource[] sources = new InvSource[list.Count];
            HashSet<int> ids = new HashSet<int>();
            int total = 0;
            for (int i = 0; i < list.Count; i++)
            {
                sources[i] = One(list[i], kind, FieldPath.Item(Field, i));
                if (!ids.Add(sources[i].Id))
                {
                    throw BridgeException.BadField(FieldPath.Join(FieldPath.Item(Field, i), "id"), "来源发货单重复");
                }
                total += sources[i].Lines.Length;
            }
            if (total > MaxLines)
            {
                throw BridgeException.BadField(Field, "各来源合计的明细必须是 1 到 200 行");
            }
            return sources;
        }

        static InvSource One(object raw, VoucherKind kind, string at)
        {
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw BridgeException.BadField(at, "来源必须是对象");
            }
            foreach (string key in map.Keys)
            {
                if (key != "id" && key != "lines")
                {
                    throw BridgeException.BadField(FieldPath.Join(at, key), "含未知字段");
                }
            }
            InvSource source = new InvSource();
            source.Id = IdOf(map, at);
            try
            {
                source.Lines = Json.GenerateLines(map, kind, false);
            }
            catch (BridgeException ex)
            {
                // field 写成 sources.<i>.lines.<j>.x。
                throw FieldPath.Under(ex, at);
            }
            return source;
        }

        static int IdOf(Dictionary<string, object> map, string at)
        {
            string field = FieldPath.Join(at, "id");
            object value;
            if (!map.TryGetValue("id", out value))
            {
                throw BridgeException.BadField(field, "缺少来源发货单 id");
            }
            long number;
            if (value is int)
            {
                number = (int)value;
            }
            else if (value is long)
            {
                number = (long)value;
            }
            else
            {
                throw BridgeException.BadField(field, "来源发货单 id 必须是整数");
            }
            if (number < 1 || number > int.MaxValue)
            {
                throw BridgeException.BadField(field, "来源发货单 id 无效");
            }
            return (int)number;
        }

        // 每张来源发货单一把 "dispatch:<DLID>"，加 "new:<kind>"。
        internal static string[] LockKeys(WorkItem item, string kind)
        {
            string[] keys = new string[item.GenSources.Length + 1];
            for (int i = 0; i < item.GenSources.Length; i++)
            {
                keys[i] = "dispatch:" + item.GenSources[i].Id.ToString(CultureInfo.InvariantCulture);
            }
            keys[item.GenSources.Length] = "new:" + kind;
            return keys;
        }

        // 单张的 id + lines 包成一个来源（n = 1）。
        internal static InvSource[] Of(WorkItem item, int sourceId, object[] lines)
        {
            if (item != null && item.GenSources != null)
            {
                return item.GenSources;
            }
            InvSource one = new InvSource();
            one.Id = sourceId;
            one.Lines = lines ?? new object[0];
            return new InvSource[] { one };
        }

        // 请求里的来源发货单 id（响应 source_ids），按请求顺序。
        internal static object[] Ids(InvSource[] sources)
        {
            object[] ids = new object[sources.Length];
            for (int i = 0; i < sources.Length; i++)
            {
                ids[i] = sources[i].Id;
            }
            return ids;
        }

        internal static int LineCount(InvSource[] sources)
        {
            int n = 0;
            for (int i = 0; i < sources.Length; i++)
            {
                n += sources[i].Lines == null ? 0 : sources[i].Lines.Length;
            }
            return n;
        }
    }
}
