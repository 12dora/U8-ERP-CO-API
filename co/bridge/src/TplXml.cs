using System;

namespace U8Co
{
    // 空白模板 DOM 与 XML 文本互转。只收没有 z:row、字段数大于 0 的 DOM；解析回来再核对字段数和无行。
    internal static class TplXml
    {
        // 不合格返回 null（不缓存）。不改动传入的 DOM。
        public static TplEntry Capture(object dom)
        {
            if (dom == null || HasRow(dom))
            {
                return null;
            }
            int fields = DomRows.Schema(dom).Count;
            string xml = Values.Text(ComUtil.Get(dom, "xml"));
            if (fields == 0 || xml.Length == 0)
            {
                return null;
            }
            TplEntry entry = new TplEntry();
            entry.Xml = xml;
            entry.Fields = fields;
            return entry;
        }

        // 每次命中都新建 DOM；任何不符都抛异常，由调用方丢弃条目并现取。
        public static object Parse(string xml, int fields)
        {
            object dom = Rows.NewDom();
            try
            {
                bool loaded = Values.Flag(ComUtil.Call(dom, "loadXML", new object[] { xml }));
                if (!loaded)
                {
                    throw new InvalidOperationException("模板缓存解析失败");
                }
                RowsDom.UseXPath(dom);
                if (HasRow(dom) || DomRows.Schema(dom).Count != fields)
                {
                    throw new InvalidOperationException("模板缓存内容不符");
                }
                object keep = dom;
                dom = null;
                return keep;
            }
            finally
            {
                ComUtil.Final(dom);
            }
        }

        static bool HasRow(object dom)
        {
            RowsDom.UseXPath(dom);
            object row = ComUtil.Call(dom, "selectSingleNode", new object[] { "//rs:data/z:row" });
            try
            {
                return row != null && !(row is DBNull);
            }
            finally
            {
                ComUtil.ReleaseOne(row);
            }
        }
    }
}
