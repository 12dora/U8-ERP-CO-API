using System;
using System.Collections.Generic;

namespace U8Co
{
    internal static partial class DomRows
    {
        static string Resolve(object dom, object row, string name, List<string> schema)
        {
            string actual = FindAttr(row, name);
            if (actual != null)
            {
                return actual;
            }
            if (schema == null)
            {
                schema = Schema(dom);
            }
            actual = Match(schema, name);
            if (actual != null)
            {
                return actual;
            }
            return name;
        }

        static string FindAttr(object row, string name)
        {
            if (row == null || name == null || name.Length == 0)
            {
                return null;
            }
            object attrs = ComUtil.Get(row, "attributes");
            try
            {
                int n = Len(attrs);
                for (int i = 0; i < n; i++)
                {
                    object attr = ComUtil.Call(attrs, "item", new object[] { i });
                    string attrName = AttrName(attr);
                    ComUtil.ReleaseOne(attr);
                    if (string.Equals(attrName, name, StringComparison.OrdinalIgnoreCase))
                    {
                        return attrName;
                    }
                }
                return null;
            }
            finally
            {
                ComUtil.ReleaseOne(attrs);
            }
        }

        static void WriteAttr(object row, string name, string value)
        {
            if (value == null)
            {
                if (FindAttr(row, name) != null)
                {
                    ComUtil.Call(row, "removeAttribute", new object[] { name });
                }
                return;
            }
            ComUtil.Call(row, "setAttribute", new object[] { name, value });
        }

        static int CopyEach(object dstDom, object dstRow, object attrs, List<string> schema, string skipCsv)
        {
            int copied = 0;
            int n = Len(attrs);
            for (int i = 0; i < n; i++)
            {
                object attr = ComUtil.Call(attrs, "item", new object[] { i });
                try
                {
                    if (CopyOne(dstDom, dstRow, attr, schema, skipCsv))
                    {
                        copied++;
                    }
                }
                finally
                {
                    ComUtil.ReleaseOne(attr);
                }
            }
            return copied;
        }

        static bool CopyOne(object dstDom, object dstRow, object attr, List<string> schema, string skipCsv)
        {
            string name = AttrName(attr);
            if (name.Length == 0 || Skipped(skipCsv, name) || Match(schema, name) == null)
            {
                return false;
            }
            Set(dstDom, dstRow, name, Values.Text(ComUtil.Get(attr, "text")), schema);
            return true;
        }

        static void PutAttr(object toRow, object attr)
        {
            string name = AttrName(attr);
            if (name.Length == 0)
            {
                return;
            }
            ComUtil.Call(toRow, "setAttribute", new object[] { name, Values.Text(ComUtil.Get(attr, "text")) });
        }

        static void DropOthers(object dom, int keep)
        {
            List<object> nodes = RowsOf(dom);
            if (keep < 0 || keep >= nodes.Count)
            {
                throw new BridgeException(500, "internal", "明细行不在单据中");
            }
            for (int i = 0; i < nodes.Count; i++)
            {
                if (i == keep)
                {
                    continue;
                }
                RemoveRow(nodes[i]);
                ComUtil.ReleaseOne(nodes[i]);
            }
        }

        static int IndexOf(object dom, object row)
        {
            if (dom == null || row == null)
            {
                return -1;
            }
            RowsDom.UseXPath(dom);
            object list = ComUtil.Call(dom, "selectNodes", new object[] { "//rs:data/z:row" });
            try
            {
                return FindIndex(list, row);
            }
            finally
            {
                ComUtil.ReleaseOne(list);
            }
        }

        // 同一个 COM 节点通常是同一个 RCW。对不上时再比 xml，避免放掉调用方的行。
        static int FindIndex(object list, object row)
        {
            int n = Len(list);
            string want = null;
            int xmlHit = -1;
            for (int i = 0; i < n; i++)
            {
                object node = ComUtil.Call(list, "item", new object[] { i });
                if (object.ReferenceEquals(node, row))
                {
                    return i;
                }
                if (want == null)
                {
                    want = Values.Text(ComUtil.Get(row, "xml"));
                }
                if (Values.Text(ComUtil.Get(node, "xml")) == want)
                {
                    xmlHit = i;
                }
            }
            return xmlHit;
        }

        static string Match(List<string> names, string name)
        {
            if (names == null)
            {
                return null;
            }
            for (int i = 0; i < names.Count; i++)
            {
                if (string.Equals(names[i], name, StringComparison.OrdinalIgnoreCase))
                {
                    return names[i];
                }
            }
            return null;
        }

        static bool Skipped(string skipCsv, string name)
        {
            if (skipCsv == null || skipCsv.Length == 0)
            {
                return false;
            }
            string[] parts = skipCsv.Split(new char[] { ',' });
            for (int i = 0; i < parts.Length; i++)
            {
                if (string.Equals(parts[i].Trim(), name, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        static string AttrName(object attr)
        {
            string name = Values.Text(ComUtil.Get(attr, "baseName")).Trim();
            if (name.Length > 0)
            {
                return name;
            }
            return Values.Text(ComUtil.Get(attr, "nodeName")).Trim();
        }

        static int Len(object list)
        {
            if (list == null || list is DBNull)
            {
                return 0;
            }
            return Convert.ToInt32(ComUtil.Get(list, "length"));
        }
    }
}
