using System.Text;

namespace U8Co
{
    // 子档案 EAI 报文的标签转义（客户联系人）。
    internal static class ArcPartnerXml
    {
        // 值里的 & < > " ' 转义，其他控制字符（制表、换行、回车除外）丢掉。
        internal static void Tag(StringBuilder sb, string tag, string value)
        {
            sb.Append('<').Append(tag).Append('>');
            string text = value ?? "";
            for (int i = 0; i < text.Length; i++)
            {
                Escape(sb, text[i]);
            }
            sb.Append("</").Append(tag).Append('>');
        }

        static void Escape(StringBuilder sb, char c)
        {
            switch (c)
            {
                case '&':
                    sb.Append("&amp;");
                    break;
                case '<':
                    sb.Append("&lt;");
                    break;
                case '>':
                    sb.Append("&gt;");
                    break;
                case '"':
                    sb.Append("&quot;");
                    break;
                case '\'':
                    sb.Append("&apos;");
                    break;
                default:
                    if (c >= ' ' || c == '\t' || c == '\n' || c == '\r')
                    {
                        sb.Append(c);
                    }
                    break;
            }
        }
    }
}
