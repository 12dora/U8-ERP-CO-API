using System;
using System.Collections.Generic;

namespace U8Co
{
    // 各子系统的许可总数（租约读不到时的回落来源）。读取实现在 LicenseHooks.ReadTotals 之后，不在本源码树中；
    // 没有实现时直接返回 null，总数只用 licenseLimits。
    // 只留「两位大写字母:整数」，不记日志、不进审计、不进响应。
    // U8 的程序集只在每次读取新建的 AppDomain 里加载（LicenseDomain），读完卸载；跨域只带回 Dictionary<string, int>。
    internal static class LicenseTotals
    {
        // 读不到（没有本机的 cStationSerial、程序集不在、调用失败、超时、结果为空）记 LastError 并返回 null，
        // 由调用方保留上一次的总数（从没读到过时是 licenseLimits）。
        public static Dictionary<string, int> Read(object conn, BridgeConfig cfg)
        {
            if (!LicenseHooks.Available())
            {
                return null;
            }
            try
            {
                string serial = LicenseSql.StationSerial(conn, Environment.MachineName);
                if (serial == null)
                {
                    LicenseState.NoteError("读许可总数：UA_TaskLog 里没有本机的 cStationSerial");
                    return null;
                }
                Dictionary<string, int> map = LicenseDomain.Run(Paths.U8Home, cfg.U8Server, serial);
                if (map != null && map.Count == 0)
                {
                    LicenseState.NoteError("读许可总数：结果为空");
                    return null;
                }
                return map;
            }
            catch (Exception ex)
            {
                LicenseState.NoteError("读许可总数失败：" + LicenseSampler.Describe(ex));
                return null;
            }
        }
    }
}
