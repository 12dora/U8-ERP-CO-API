using System;
using System.Collections.Generic;

namespace U8Co
{
    internal static class RowsDom
    {
        const string RowNs = "xmlns:rs='urn:schemas-microsoft-com:rowset' xmlns:z='#RowsetSchema'";
        const string SelectNs = RowNs + " xmlns:s='uuid:BDC6E3F0-6DA3-11d1-A2A3-00AA00C14882'"
            + " xmlns:dt='uuid:C2F41010-65B3-11d1-A29F-00AA00C14882'";

        public static object NewDom()
        {
            object dom = ComUtil.Create("MSXML2.DOMDocument");
            if (dom == null)
            {
                throw new BridgeException(503, "com_unavailable", "MSXML 未注册");
            }
            try
            {
                ComUtil.Set(dom, "async", false);
                UseXPath(dom);
                return dom;
            }
            catch
            {
                ComUtil.Final(dom);
                throw;
            }
        }

        // MSXML 3 默认按 XSLPattern 解析，local-name() 等 XPath 函数会报「方法未知」。
        public static void UseXPath(object dom)
        {
            ComUtil.Call(dom, "setProperty", new object[] { "SelectionLanguage", "XPath" });
            ComUtil.Call(dom, "setProperty", new object[] { "SelectionNamespaces", SelectNs });
        }

        public static List<Dictionary<string, object>> FromDom(object dom, int maxRows)
        {
            if (dom == null)
            {
                throw new BridgeException(500, "internal", "DOM 为空");
            }
            if (maxRows < 1)
            {
                return new List<Dictionary<string, object>>();
            }
            ComUtil.Call(dom, "setProperty", new object[] { "SelectionNamespaces", RowNs });
            object list = null;
            try
            {
                list = ComUtil.Call(dom, "selectNodes", new object[] { "//z:row" });
                return ReadNodes(list, maxRows);
            }
            finally
            {
                ComUtil.ReleaseOne(list);
            }
        }

        static List<Dictionary<string, object>> ReadNodes(object list, int maxRows)
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            if (list == null)
            {
                return rows;
            }
            int count = Convert.ToInt32(ComUtil.Get(list, "length"));
            int take = count < maxRows ? count : maxRows;
            for (int i = 0; i < take; i++)
            {
                object node = null;
                try
                {
                    node = ComUtil.Call(list, "item", new object[] { i });
                    rows.Add(ReadAttrs(node));
                }
                finally
                {
                    ComUtil.ReleaseOne(node);
                }
            }
            return rows;
        }

        static Dictionary<string, object> ReadAttrs(object node)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            object attrs = null;
            try
            {
                attrs = ComUtil.Get(node, "attributes");
                int count = Convert.ToInt32(ComUtil.Get(attrs, "length"));
                for (int i = 0; i < count; i++)
                {
                    PutAttr(row, attrs, i);
                }
                return row;
            }
            finally
            {
                ComUtil.ReleaseOne(attrs);
            }
        }

        static void PutAttr(Dictionary<string, object> row, object attrs, int index)
        {
            object attr = null;
            try
            {
                attr = ComUtil.Call(attrs, "item", new object[] { index });
                string name = Values.Text(ComUtil.Get(attr, "nodeName"));
                if (name.Length == 0 || Rows.Hidden(name))
                {
                    return;
                }
                row[name] = Values.Text(ComUtil.Get(attr, "text"));
            }
            finally
            {
                ComUtil.ReleaseOne(attr);
            }
        }
    }
}
