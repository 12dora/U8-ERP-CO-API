using System;
using System.Collections.Generic;

namespace U8Co
{
    // 点数租约与许可总数的读取入口。点数租约读取的实现不在本源码树中；没有实现时只用 UA_TaskLog。
    // 实现以同名分部类提供下面的分部方法；没有编译进来时这些方法连同调用一起被编译器去掉，
    // 许可总数只用 config.json 的 licenseLimits，--check-config 写明「此版本不含点数租约读取」。
    internal static partial class LicenseHooks
    {
        public const string Missing = "此版本不含点数租约读取";

        static partial void Present(ref bool present);

        // 只在采样线程上调用；读不到时 snap 留 null、reason 给固定文字。实现自己接住异常。
        static partial void LeaseCall(BridgeConfig cfg, ref LicenseLeaseSnapshot snap, ref string reason);

        // 只在 LicenseWorker 所在的专用 AppDomain 里调用；读失败直接抛异常。
        static partial void TotalsCall(string home, string appServer, string serial, ref Dictionary<string, int> map);

        static partial void PrivateSelfTest(ref bool ran);

        public static bool Available()
        {
            bool present = false;
            Present(ref present);
            return present;
        }

        public static bool TryReadLeases(BridgeConfig cfg, out LicenseLeaseSnapshot snap, out string reason)
        {
            snap = null;
            reason = Missing;
            if (!Available())
            {
                return false;
            }
            LicenseLeaseSnapshot got = null;
            string why = null;
            LeaseCall(cfg, ref got, ref why);
            snap = got;
            reason = got != null ? null : (why ?? "读租约没有结果");
            return got != null;
        }

        public static Dictionary<string, int> ReadTotals(string home, string appServer, string serial)
        {
            Dictionary<string, int> map = null;
            TotalsCall(home, appServer, serial, ref map);
            if (map == null)
            {
                throw new InvalidOperationException(Available() ? "读许可总数没有结果" : Missing);
            }
            return map;
        }

        // 读取实现的离线自检（在时）；不在时核对公开部分的回落：租约读不到、原因是 Missing。
        public static void SelfTest()
        {
            bool ran = false;
            PrivateSelfTest(ref ran);
            if (ran || Available())
            {
                return;
            }
            LicenseLeaseSnapshot snap;
            string reason;
            if (TryReadLeases(new BridgeConfig(), out snap, out reason) || snap != null || reason != Missing)
            {
                throw new InvalidOperationException("license hooks missing");
            }
        }
    }
}
