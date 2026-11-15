using System;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text;

namespace U8Co
{
    internal sealed class AuthEntry
    {
        public string ConnString;
        public string OperatorName;
        public long StoredTicks;
        public long ExpiresTicks;
        // 被 Forget 删掉后置真。读线程执行前用 AuthCache.Live 再看一次。
        public bool Dead;
    }

    // 写线程登录成功后记下连接串，读线程凭它直接查库，不再登录。
    // 键是进程随机密钥上的 HMAC，口令本身不留在内存表里。连接串不写日志、审计和响应。
    internal static class AuthCache
    {
        const int MaxEntries = 256;
        static readonly byte[] ProcessKey = NewKey();
        static readonly object Gate = new object();
        static readonly Dictionary<string, AuthEntry> Map = new Dictionary<string, AuthEntry>(StringComparer.Ordinal);
        static int _ttlSeconds;

        public static void Configure(int seconds)
        {
            lock (Gate)
            {
                _ttlSeconds = seconds < 0 ? 0 : seconds;
                Map.Clear();
            }
        }

        public static bool Enabled
        {
            get
            {
                lock (Gate)
                {
                    return _ttlSeconds > 0;
                }
            }
        }

        public static string KeyOf(WorkItem item, string subId)
        {
            byte[] data = KeyBytes(item, subId);
            try
            {
                using (HMACSHA256 mac = new HMACSHA256(ProcessKey))
                {
                    return Convert.ToBase64String(mac.ComputeHash(data));
                }
            }
            finally
            {
                Array.Clear(data, 0, data.Length);
            }
        }

        public static AuthEntry Find(string key)
        {
            if (key == null)
            {
                return null;
            }
            lock (Gate)
            {
                AuthEntry entry;
                if (!Map.TryGetValue(key, out entry))
                {
                    return null;
                }
                if (DateTime.UtcNow.Ticks >= entry.ExpiresTicks)
                {
                    Map.Remove(key);
                    return null;
                }
                return entry;
            }
        }

        public static void Store(string key, string connString, string operatorName)
        {
            if (key == null || connString == null || connString.Length == 0)
            {
                return;
            }
            lock (Gate)
            {
                if (_ttlSeconds <= 0)
                {
                    return;
                }
                long now = DateTime.UtcNow.Ticks;
                Map.Remove(key);
                Purge(now);
                AuthEntry entry = new AuthEntry();
                entry.ConnString = connString;
                entry.OperatorName = operatorName ?? "";
                entry.StoredTicks = now;
                entry.ExpiresTicks = now + _ttlSeconds * TimeSpan.TicksPerSecond;
                Map[key] = entry;
            }
        }

        public static void Forget(string key)
        {
            if (key == null)
            {
                return;
            }
            lock (Gate)
            {
                AuthEntry entry;
                if (Map.TryGetValue(key, out entry))
                {
                    entry.Dead = true;
                    Map.Remove(key);
                }
            }
        }

        // 入队时命中的条目，执行前再确认没过期、没被删掉。
        public static bool Live(AuthEntry entry)
        {
            if (entry == null)
            {
                return false;
            }
            lock (Gate)
            {
                return _ttlSeconds > 0 && !entry.Dead && DateTime.UtcNow.Ticks < entry.ExpiresTicks;
            }
        }

        // 调用方持有 Gate。先丢过期的，仍满就丢最早记下的。
        static void Purge(long now)
        {
            List<string> stale = new List<string>();
            string oldest = null;
            long oldestTicks = long.MaxValue;
            foreach (KeyValuePair<string, AuthEntry> pair in Map)
            {
                if (now >= pair.Value.ExpiresTicks)
                {
                    stale.Add(pair.Key);
                }
                else if (pair.Value.StoredTicks < oldestTicks)
                {
                    oldestTicks = pair.Value.StoredTicks;
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

        // 每段带长度前缀，口令里有分隔符也不会和别的组合撞键。登录日期也进键，命中缓存不会绕过 U8 的日期检查。
        static byte[] KeyBytes(WorkItem item, string subId)
        {
            StringBuilder buf = new StringBuilder();
            Part(buf, item.Acc);
            Part(buf, item.Year);
            Part(buf, item.Operator);
            Part(buf, item.Password);
            Part(buf, subId);
            Part(buf, item.Date);
            byte[] data = Encoding.UTF8.GetBytes(buf.ToString());
            buf.Length = 0;
            return data;
        }

        static void Part(StringBuilder buf, string value)
        {
            string text = value ?? "";
            buf.Append(text.Length);
            buf.Append(':');
            buf.Append(text);
            buf.Append('|');
        }

        static byte[] NewKey()
        {
            byte[] key = new byte[32];
            using (RNGCryptoServiceProvider rng = new RNGCryptoServiceProvider())
            {
                rng.GetBytes(key);
            }
            return key;
        }
    }
}
