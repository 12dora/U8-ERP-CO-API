using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的权限快照缓存部分（PermCache、permCacheSeconds）：缺省 60、范围 0 到 3600、0 不缓存也不留条目、
    // 健康检查与 perm/snapshot 的 ttl_s 取当前生效值。不连库。由 PermSelfTest.Run 调用。
    internal static class PermCacheSelfTest
    {
        public static void Run()
        {
            CheckConfig();
            int was = PermCache.Seconds;
            try
            {
                CheckZero();
                CheckTtl();
                CheckHealth();
            }
            finally
            {
                PermCache.Configure(was);
                PermCache.Clear();
            }
        }

        static void CheckConfig()
        {
            BridgeConfig cfg = new BridgeConfig();
            Expect("cfg default field", cfg.PermCacheSeconds == 60 && PermCache.DefaultSeconds == 60);
            Dictionary<string, object> map = new Dictionary<string, object>();
            BridgeConfig.LoadPools(cfg, map);
            Expect("cfg default", cfg.PermCacheSeconds == 60);
            map["permCacheSeconds"] = 0;
            BridgeConfig.LoadPools(cfg, map);
            Expect("cfg zero", cfg.PermCacheSeconds == 0);
            map["permCacheSeconds"] = 3600;
            BridgeConfig.LoadPools(cfg, map);
            Expect("cfg max", cfg.PermCacheSeconds == 3600);
            ExpectBad("cfg over", 3601);
            ExpectBad("cfg negative", -1);
            ExpectBad("cfg text", "60");
            Dictionary<string, object> keys = new Dictionary<string, object>();
            keys["permCacheSeconds"] = 60;
            BridgeConfig.CheckKeys(keys);
        }

        // 0：每次查询都读库，不留条目，也不会把旧条目当命中。
        static void CheckZero()
        {
            int loads = 0;
            Func<PermContext> load = delegate { loads++; return Fresh(); };
            PermCache.Configure(60);
            PermCache.Clear();
            PermCache.Get("998", 2024, 2024, "op1", load);
            PermCache.Get("998", 2024, 2024, "op1", load);
            Expect("ttl 60 hits", loads == 1 && PermCache.Count == 1);
            PermCache.Configure(0);
            PermCache.Get("998", 2024, 2024, "op1", load);
            PermCache.Get("998", 2024, 2024, "op1", load);
            Expect("ttl 0 loads each time", loads == 3);
            PermCache.Clear();
            PermCache.Get("998", 2024, 2024, "op1", load);
            Expect("ttl 0 stores nothing", loads == 4 && PermCache.Count == 0);
        }

        // 非 0：过期按配置秒数，已过期的条目重新读。
        static void CheckTtl()
        {
            int loads = 0;
            Func<PermContext> aged = delegate
            {
                loads++;
                PermContext p = Fresh();
                p.LoadedTicks -= 30L * TimeSpan.TicksPerSecond;
                return p;
            };
            PermCache.Configure(60);
            PermCache.Clear();
            PermCache.Get("998", 2024, 2024, "op1", aged);
            PermCache.Get("998", 2024, 2024, "op1", aged);
            Expect("ttl 60 still fresh at 30s", loads == 1);
            PermCache.Configure(10);
            PermCache.Get("998", 2024, 2024, "op1", aged);
            Expect("ttl 10 expired at 30s", loads == 2);
        }

        static void CheckHealth()
        {
            PermCache.Configure(0);
            Expect("health zero", PermCache.HealthFragment() == ",\"perm_cache_s\":0");
            PermCache.Configure(60);
            Expect("health default", PermCache.HealthFragment() == ",\"perm_cache_s\":60");
            PermCache.Configure(7);
            PermContext p = Fresh();
            Expect("snapshot ttl live", (int)PermSnapshot.ToJson(p)["ttl_s"] == 7);
        }

        static PermContext Fresh()
        {
            PermContext p = new PermContext();
            p.Acc = "998";
            p.Operator = "op1";
            p.Year = 2024;
            p.DateYear = 2024;
            p.AcctYear = 2024;
            p.LoadedTicks = DateTime.UtcNow.Ticks;
            return p;
        }

        static void ExpectBad(string name, object value)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["permCacheSeconds"] = value;
            try
            {
                BridgeConfig.LoadPools(new BridgeConfig(), map);
            }
            catch (InvalidOperationException ex)
            {
                Expect(name + " message", ex.Message.Contains("permCacheSeconds"));
                return;
            }
            Expect(name, false);
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("perm cache " + name);
            }
        }
    }
}
