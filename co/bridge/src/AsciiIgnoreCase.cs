using System;

namespace U8Co
{
    // 只读例外的操作员比较规则（与 API readonly_exceptions.ascii_fold 一致）：只把 A–Z 当作 a–z，
    // 其余字符（含非 ASCII）逐字比较。不用 OrdinalIgnoreCase：它按 Unicode 大小写映射，和 API 的规则不同，
    // 两侧对同一个操作员可能一个放行、一个拒绝。
    internal sealed class AsciiIgnoreCase : StringComparer
    {
        internal static readonly AsciiIgnoreCase Instance = new AsciiIgnoreCase();

        AsciiIgnoreCase()
        {
        }

        internal static string Fold(string text)
        {
            if (text == null)
            {
                return null;
            }
            char[] chars = text.ToCharArray();
            for (int i = 0; i < chars.Length; i++)
            {
                if (chars[i] >= 'A' && chars[i] <= 'Z')
                {
                    chars[i] = (char)(chars[i] + ('a' - 'A'));
                }
            }
            return new string(chars);
        }

        public override int Compare(string x, string y)
        {
            return string.CompareOrdinal(Fold(x), Fold(y));
        }

        public override bool Equals(string x, string y)
        {
            return string.Equals(Fold(x), Fold(y), StringComparison.Ordinal);
        }

        public override int GetHashCode(string obj)
        {
            return obj == null ? 0 : Fold(obj).GetHashCode();
        }
    }
}
