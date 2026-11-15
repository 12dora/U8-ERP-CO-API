using System;
using System.Collections.Generic;

namespace U8Co
{
    // 空白模板缓存：只缓存 ADO「where 1=2」持久化出来的纯 schema DOM（DomRows.Blank 一处接入）。
    // 存 XML 文本，每次命中都 loadXML 出新的 DOM，缓存里没有 DOM 实例可被改写。
    // 每次使用都先核对：数据库对象指纹（结果集元数据的 SHA-256）、ADO/MSXML 组件指纹、30 分钟硬 TTL。
    // 任何一步出错都不用缓存、不写缓存，直接现取。只在内存里，进程重启即清空。
    // 不缓存：GetDefaultVoucherDom、GetDefaultVTID、ArcTpl 行、FillHead/StampHead 之后的 DOM；
    // CO 组件返回的空白（GetVoucherDataById(…,0,…)、UFAPBO GetVouchData）也不缓存：跳过这次调用后
    // 组件对象内部是否还有保存要用的状态，离线无法核实。
    internal static class TplCache
    {
        const int TtlMinutes = 30;
        const int MaxEntries = 64;
        // 指纹查询出错后暂停缓存一段时间，不在每个请求上重复失败的查询。
        const int CooldownMinutes = 10;

        static readonly object Gate = new object();
        static readonly Dictionary<string, TplEntry> Items = new Dictionary<string, TplEntry>(StringComparer.Ordinal);
        static bool _enabled = true;
        static DateTime _coolUntil = DateTime.MinValue;

        // 服务启动时按 config.json 的 templateCache 设置。
        public static void Configure(bool enabled)
        {
            lock (Gate)
            {
                _enabled = enabled;
                Items.Clear();
            }
        }

        // load 必须是「连接上现取一次空白 DOM」。返回的 DOM 归调用方所有。
        public static object AdoBlank(object conn, string sql, Func<object> load)
        {
            if (!Usable())
            {
                return load();
            }
            TplProbe before = Probe(conn, sql);
            if (before == null)
            {
                return load();
            }
            object hit = TryHit(before);
            if (hit != null)
            {
                return hit;
            }
            object dom = load();
            Store(conn, sql, before, dom);
            return dom;
        }

        static bool Usable()
        {
            lock (Gate)
            {
                return _enabled && DateTime.UtcNow >= _coolUntil;
            }
        }

        static TplProbe Probe(object conn, string sql)
        {
            try
            {
                return TplFingerprint.Probe(conn, sql);
            }
            catch (Exception)
            {
                CoolDown();
                return null;
            }
        }

        static void CoolDown()
        {
            lock (Gate)
            {
                _coolUntil = DateTime.UtcNow.AddMinutes(CooldownMinutes);
                Items.Clear();
            }
        }

        static object TryHit(TplProbe probe)
        {
            TplEntry entry = Fresh(probe);
            if (entry == null)
            {
                return null;
            }
            object dom = null;
            try
            {
                dom = TplXml.Parse(entry.Xml, entry.Fields);
                object keep = dom;
                dom = null;
                return keep;
            }
            catch (Exception)
            {
                Drop(probe.Key);
                return null;
            }
            finally
            {
                ComUtil.Final(dom);
            }
        }

        // 键、两种指纹都相同且未过期才算命中；过期或不符的条目当场删掉。
        static TplEntry Fresh(TplProbe probe)
        {
            lock (Gate)
            {
                TplEntry entry;
                if (!Items.TryGetValue(probe.Key, out entry))
                {
                    return null;
                }
                bool ok = entry.SchemaFp == probe.SchemaFp && entry.ComponentFp == probe.ComponentFp
                    && DateTime.UtcNow - entry.CreatedUtc < TimeSpan.FromMinutes(TtlMinutes)
                    && DateTime.UtcNow >= entry.CreatedUtc;
                if (!ok)
                {
                    Items.Remove(probe.Key);
                    return null;
                }
                return entry;
            }
        }

        static void Drop(string key)
        {
            lock (Gate)
            {
                Items.Remove(key);
            }
        }

        // 取模板之前、之后各核对一次指纹，两次一致才写入：取的过程中表结构或组件变了就不缓存。
        static void Store(object conn, string sql, TplProbe before, object dom)
        {
            try
            {
                TplEntry entry = TplXml.Capture(dom);
                if (entry == null)
                {
                    return;
                }
                TplProbe after = TplFingerprint.Probe(conn, sql);
                if (after == null || !before.SameAs(after))
                {
                    return;
                }
                entry.SchemaFp = before.SchemaFp;
                entry.ComponentFp = before.ComponentFp;
                entry.CreatedUtc = DateTime.UtcNow;
                Put(before.Key, entry);
            }
            catch (Exception)
            {
                CoolDown();
            }
        }

        static void Put(string key, TplEntry entry)
        {
            lock (Gate)
            {
                if (!_enabled)
                {
                    return;
                }
                if (Items.Count >= MaxEntries && !Items.ContainsKey(key))
                {
                    Items.Clear();
                }
                Items[key] = entry;
            }
        }
    }

    internal sealed class TplEntry
    {
        public string Xml;
        public int Fields;
        public string SchemaFp;
        public string ComponentFp;
        public DateTime CreatedUtc;
    }

    internal sealed class TplProbe
    {
        public string Key;
        public string SchemaFp;
        public string ComponentFp;

        public bool SameAs(TplProbe other)
        {
            return other != null && Key == other.Key && SchemaFp == other.SchemaFp && ComponentFp == other.ComponentFp;
        }
    }
}
