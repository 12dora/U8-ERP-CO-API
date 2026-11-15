using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // config.json 的 wedgedLoginRecycle（0 到 10，缺省 2，0 关闭）与 wedgedLoginRestart（true / false，不写为缺省），
    // 以及 --check-config 的说明。见 WedgedLogin。
    internal static class WedgedRules
    {
        public const int MaxRecycle = 10;

        public static void Load(BridgeConfig cfg, Dictionary<string, object> map)
        {
            cfg.WedgedLoginRecycle = BridgeConfig.OptionalInt(map, "wedgedLoginRecycle", 2, 0, MaxRecycle);
            if (map.ContainsKey("wedgedLoginRestart"))
            {
                cfg.WedgedLoginRestart = ConfigRules.OptionalBool(map, "wedgedLoginRestart", false);
            }
            else
            {
                cfg.WedgedLoginRestart = null;
            }
        }

        // --check-config 的两行。scm 是 --service-name（缺省 u8co）那个服务的失败动作（WedgedRestart.FailureMode，查不到为 null）。
        public static string[] Describe(BridgeConfig cfg, string serviceName, ScmPolicy scm)
        {
            string recycle = cfg.WedgedLoginRecycle.ToString(CultureInfo.InvariantCulture)
                + (cfg.WedgedLoginRecycle == 0 ? "（关闭）" : "");
            string restart;
            if (!cfg.WedgedLoginRestart.HasValue)
            {
                restart = "缺省（以服务运行且服务的第一条失败动作为重启时开）";
            }
            else
            {
                restart = cfg.WedgedLoginRestart.Value ? "true" : "false";
            }
            restart += "；控制台模式一律不重启；距桥上一次结束进程至少 30 分钟";
            // 失败动作全部为重启时不留，ScmLabel 写明「全部为重启，第二步不按失败次数封顶（仍间隔 30 分钟）」。
            if (scm == null || !scm.Unlimited)
            {
                restart += "，并按服务失败计数留一次重启给看门狗卡死";
            }
            return new string[]
            {
                "wedgedLoginRecycle（登录状态不是 0 自愈：连续几次判为卡住后换写线程）: " + recycle,
                "wedgedLoginRestart（换线程后仍卡住时重启进程）: " + restart + "；服务 " + serviceName + " 的失败动作："
                    + WedgedRestart.ScmLabel(scm)
            };
        }
    }
}
