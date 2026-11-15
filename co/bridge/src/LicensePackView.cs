using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 健康检查里的 license_source 和 license_packs：
    // ,"license_source":"leases","license_packs":{"XX":{"used":3,"limit":10,"modules":["PU","SA","ST"]}}（示例数字）
    // 只有两位码和整数；不知道的 limit 省略。最多 32 个包、每包 16 个模块。
    internal static class LicensePackView
    {
        const int MaxPacks = 32;
        const int MaxModules = 16;

        // 产品包、模块的两位码：大写字母或数字（U8 这类含数字的也算）。
        public static bool Code(string text)
        {
            return text != null && text.Length == 2 && CodeChar(text[0]) && CodeChar(text[1]);
        }

        static bool CodeChar(char c)
        {
            return (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9');
        }

        public static void Append(StringBuilder buf, string source, Dictionary<string, LicensePack> packs)
        {
            bool leases = source == LicenseState.SourceLeases;
            buf.Append(",\"license_source\":\"").Append(leases ? LicenseState.SourceLeases : LicenseState.SourceTaskLog);
            buf.Append("\",\"license_packs\":{");
            if (leases)
            {
                AppendPacks(buf, packs);
            }
            buf.Append('}');
        }

        static void AppendPacks(StringBuilder buf, Dictionary<string, LicensePack> packs)
        {
            List<string> keys = new List<string>(packs.Keys);
            keys.Sort(StringComparer.Ordinal);
            int written = 0;
            for (int i = 0; i < keys.Count && written < MaxPacks; i++)
            {
                if (!Code(keys[i]))
                {
                    continue;
                }
                if (written > 0)
                {
                    buf.Append(',');
                }
                written++;
                AppendPack(buf, keys[i], packs[keys[i]]);
            }
        }

        // 码已按 Code 过滤（两位大写字母或数字），不需要转义。
        static void AppendPack(StringBuilder buf, string code, LicensePack pack)
        {
            buf.Append('"').Append(code).Append("\":{\"used\":").Append(Num(Math.Max(0, pack.Used)));
            if (pack.Limit >= 0)
            {
                buf.Append(",\"limit\":").Append(Num(pack.Limit));
            }
            buf.Append(",\"modules\":[");
            int written = 0;
            for (int i = 0; i < pack.Modules.Count && written < MaxModules; i++)
            {
                if (!Code(pack.Modules[i]))
                {
                    continue;
                }
                if (written > 0)
                {
                    buf.Append(',');
                }
                written++;
                buf.Append('"').Append(pack.Modules[i]).Append('"');
            }
            buf.Append("]}");
        }

        static string Num(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }
}
