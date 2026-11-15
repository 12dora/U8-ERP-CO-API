using System;
using System.Collections.Generic;

namespace U8Co
{
    // 权限快照缓存：键 = 账套 + 请求年度 + 登录年度 + 操作员，60 秒过期，最多 256 条。
    // 只在登录成功（或登录缓存命中）之后读，所以命中缓存不会绕过口令检查。登录失败时 StaExec 调 Forget 清掉该操作员。
    // 过期后重建条目时，数据权限开关、授权都重新读。
    internal static class PermCache
    {
        const int MaxEntries = 256;
        const long TtlTicks = 60L * TimeSpan.TicksPerSecond;
        static readonly object Gate = new object();
        static readonly Dictionary<string, PermContext> Map = new Dictionary<string, PermContext>(StringComparer.Ordinal);

        public static PermContext Get(object conn, string acc, int year, int dateYear, string op)
        {
            string key = KeyOf(acc, year, dateYear, op);
            long now = DateTime.UtcNow.Ticks;
            lock (Gate)
            {
                PermContext hit;
                if (Map.TryGetValue(key, out hit))
                {
                    if (now - hit.LoadedTicks < TtlTicks && now >= hit.LoadedTicks)
                    {
                        return hit;
                    }
                    Map.Remove(key);
                }
            }
            // 读库在锁外：同一操作员并发时各读一次，最后写入的留下，结果相同。
            PermContext fresh = PermLoad.Load(conn, acc, year, dateYear, op);
            lock (Gate)
            {
                Purge(DateTime.UtcNow.Ticks);
                Map[key] = fresh;
            }
            return fresh;
        }

        // 登录失败：删掉该账套、该操作员的所有年度的条目。
        public static void Forget(string acc, string op)
        {
            string prefix = Part(acc) + "|";
            string tail = "|" + Part(Upper(op));
            lock (Gate)
            {
                List<string> drop = new List<string>();
                foreach (string key in Map.Keys)
                {
                    if (key.StartsWith(prefix, StringComparison.Ordinal) && key.EndsWith(tail, StringComparison.Ordinal))
                    {
                        drop.Add(key);
                    }
                }
                for (int i = 0; i < drop.Count; i++)
                {
                    Map.Remove(drop[i]);
                }
            }
        }

        public static void Clear()
        {
            lock (Gate)
            {
                Map.Clear();
            }
        }

        // 调用方持有 Gate。先丢过期的，仍满就丢最早读的。
        static void Purge(long now)
        {
            List<string> stale = new List<string>();
            string oldest = null;
            long oldestTicks = long.MaxValue;
            foreach (KeyValuePair<string, PermContext> pair in Map)
            {
                if (now - pair.Value.LoadedTicks >= TtlTicks)
                {
                    stale.Add(pair.Key);
                }
                else if (pair.Value.LoadedTicks < oldestTicks)
                {
                    oldestTicks = pair.Value.LoadedTicks;
                    oldest = pair.Key;
                }
            }
            for (int i = 0; i < stale.Count; i++)
            {
                Map.Remove(stale[i]);
            }
            if (Map.Count >= MaxEntries && oldest != null)
            {
                Map.Remove(oldest);
            }
        }

        // 每段带长度前缀，操作员里有分隔符也不会撞键。操作员不分大小写（U8 登录不分）。
        internal static string KeyOf(string acc, int year, int dateYear, string op)
        {
            return Part(acc) + "|" + year.ToString(System.Globalization.CultureInfo.InvariantCulture) + "|"
                + dateYear.ToString(System.Globalization.CultureInfo.InvariantCulture) + "|" + Part(Upper(op));
        }

        static string Upper(string text)
        {
            return (text ?? "").ToUpperInvariant();
        }

        static string Part(string text)
        {
            string value = text ?? "";
            return value.Length.ToString(System.Globalization.CultureInfo.InvariantCulture) + ":" + value;
        }
    }
}
