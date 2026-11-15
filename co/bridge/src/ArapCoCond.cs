using System.Text;

namespace U8Co
{
    // UFAPBO 的 XmlCondition，按单据族各一个小函数，实测后只改这里。
    // 审核、弃审只用单张单据 type='0'；type='1' 是按日期批量，永远不发。
    internal static class ArapCond
    {
        public static string Template(ArapSpec spec)
        {
            return "<condition" + Attr("cVouchType", spec.VouchType) + "/>";
        }

        // 48/49 按 iID；R0/P0 按 cLink（= cVouchType + cVouchID）。
        public static string Load(ArapSpec spec, ArapDoc doc)
        {
            if (spec.Close)
            {
                return "<condition keytype='1'" + Attr("iID", Id(doc)) + "/>";
            }
            return "<condition keytype='1'" + Attr("cLink", doc.Link) + "/>";
        }

        public static string Sign(ArapSpec spec, ArapDoc doc)
        {
            if (spec.Close)
            {
                return "<condition type='0'" + Attr("iID", Id(doc)) + "/>";
            }
            return VouchSign(spec, doc);
        }

        public static string Unsign(ArapSpec spec, ArapDoc doc)
        {
            if (spec.Close)
            {
                return "<condition type='0'" + Attr("cVouchType", spec.VouchType) + Attr("cVouchID", doc.Code)
                    + Attr("iID", Id(doc)) + "/>";
            }
            return VouchSign(spec, doc);
        }

        // 实测：R0/P0 审核与弃审用同一条件。
        static string VouchSign(ArapSpec spec, ArapDoc doc)
        {
            return "<condition type='0'" + Attr("cLink", doc.Link) + Attr("cVouchType", spec.VouchType)
                + Attr("cVouchID", doc.Code) + "/>";
        }

        public static string Delete(ArapSpec spec, ArapDoc doc)
        {
            if (spec.Close)
            {
                return "<condition keytype='2'" + Attr("iID", Id(doc)) + "/>";
            }
            return "<condition keytype='2'" + Attr("cVouchType", spec.VouchType) + Attr("cVouchID", doc.Code) + "/>";
        }

        static string Id(ArapDoc doc)
        {
            return doc.Id.ToString(System.Globalization.CultureInfo.InvariantCulture);
        }

        static string Attr(string name, string value)
        {
            if (value == null || value.Length == 0)
            {
                throw new BridgeException(500, "internal", "单据条件缺少 " + name);
            }
            return " " + name + "='" + Escape(value) + "'";
        }

        static string Escape(string text)
        {
            StringBuilder buf = new StringBuilder(text.Length);
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (c == '&')
                {
                    buf.Append("&amp;");
                }
                else if (c == '<')
                {
                    buf.Append("&lt;");
                }
                else if (c == '\'')
                {
                    buf.Append("&apos;");
                }
                else if (c == '"')
                {
                    buf.Append("&quot;");
                }
                else
                {
                    buf.Append(c);
                }
            }
            return buf.ToString();
        }
    }
}
