using System;
using System.Collections.Generic;

namespace U8Co
{
    // 一个产品包的点数：Used = 属于这个包且占点的数，Limit = 这个包的许可总数（-1 为不知道）。
    internal sealed class LicensePack
    {
        public int Used;
        public int Limit = -1;
        public List<string> Modules = new List<string>();
        public Dictionary<string, int> ModuleCounts = new Dictionary<string, int>(StringComparer.Ordinal);

        public LicensePack Copy()
        {
            LicensePack copy = (LicensePack)MemberwiseClone();
            copy.Modules = new List<string>(Modules);
            copy.ModuleCounts = new Dictionary<string, int>(ModuleCounts, StringComparer.Ordinal);
            return copy;
        }
    }

    // 一次租约采样的结果：只有码和数字。Modules 的值是 {used, limit}：包内模块取所在包的数，独立模块取自己的。
    internal sealed class LicenseLeaseSnapshot
    {
        public Dictionary<string, LicensePack> Packs = new Dictionary<string, LicensePack>(StringComparer.Ordinal);
        public Dictionary<string, int[]> Modules = new Dictionary<string, int[]>(StringComparer.Ordinal);
        // 本机（桥所在计算机）名下的租约数。
        public int BridgeRows;
    }

    // 读不到租约时的固定原因。消息都是代码里写死的文字，不带外部回复原文，可以进审计。
    internal sealed class LicenseLeaseError : Exception
    {
        public LicenseLeaseError(string message)
            : base(message)
        {
        }
    }
}
