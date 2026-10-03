using System;
using System.Collections.Generic;

namespace U8Co
{
    // 把表头表体行克隆进销售订单模板 DOM。
    internal static partial class SoDom
    {
        static void Fill(object dom, List<Dictionary<string, string>> rows)
        {
            Prepare(dom);
            object data = DataNode(dom);
            try
            {
                object seed = EnsureSeed(dom, data);
                try
                {
                    TrimExtra(data);
                    CloneTo(data, seed, rows.Count);
                    WriteAll(data, rows);
                }
                finally
                {
                    ComUtil.Final(seed);
                }
            }
            finally
            {
                ComUtil.Final(data);
            }
        }

        static object DataNode(object dom)
        {
            object data = ComUtil.Call(dom, "selectSingleNode", new object[] { "//rs:data" });
            if (data != null && !(data is DBNull))
            {
                return data;
            }
            data = ComUtil.Call(dom, "createNode", new object[] { 1, "rs:data", "urn:schemas-microsoft-com:rowset" });
            object root = ComUtil.Get(dom, "documentElement");
            if (root == null)
            {
                ComUtil.Final(data);
                throw new BridgeException(500, "internal", "模板没有字段");
            }
            ComUtil.Call(root, "appendChild", new object[] { data });
            ComUtil.ReleaseOne(root);
            return data;
        }

        static object EnsureSeed(object dom, object data)
        {
            object list = ComUtil.Call(dom, "selectNodes", new object[] { "//rs:data/z:row" });
            try
            {
                if (Len(list) > 0)
                {
                    return ComUtil.Call(list, "item", new object[] { 0 });
                }
            }
            finally
            {
                ComUtil.Final(list);
            }
            object row = ComUtil.Call(dom, "createNode", new object[] { 1, "z:row", "#RowsetSchema" });
            ComUtil.Call(data, "appendChild", new object[] { row });
            return row;
        }

        static void TrimExtra(object data)
        {
            for (int guard = 0; guard < 300; guard++)
            {
                object list = ChildRows(data);
                try
                {
                    int n = Len(list);
                    if (n <= 1)
                    {
                        return;
                    }
                    DropRow(data, list, n - 1);
                }
                finally
                {
                    ComUtil.Final(list);
                }
            }
            throw new BridgeException(500, "internal", "模板行数不符");
        }

        static void DropRow(object data, object list, int index)
        {
            object row = ComUtil.Call(list, "item", new object[] { index });
            try
            {
                ComUtil.Call(data, "removeChild", new object[] { row });
            }
            finally
            {
                ComUtil.Final(row);
            }
        }

        static void CloneTo(object data, object seed, int count)
        {
            for (int i = 1; i < count; i++)
            {
                object copy = ComUtil.Call(seed, "cloneNode", new object[] { true });
                try
                {
                    ComUtil.Call(data, "appendChild", new object[] { copy });
                }
                finally
                {
                    ComUtil.Final(copy);
                }
            }
        }

        static void WriteAll(object data, List<Dictionary<string, string>> rows)
        {
            object list = ChildRows(data);
            try
            {
                if (Len(list) != rows.Count)
                {
                    throw new BridgeException(500, "internal", "模板行数不符");
                }
                for (int i = 0; i < rows.Count; i++)
                {
                    WriteAt(list, i, rows[i]);
                }
            }
            finally
            {
                ComUtil.Final(list);
            }
        }

        static void WriteAt(object list, int index, Dictionary<string, string> values)
        {
            object row = ComUtil.Call(list, "item", new object[] { index });
            try
            {
                foreach (KeyValuePair<string, string> pair in values)
                {
                    ComUtil.Call(row, "setAttribute", new object[] { pair.Key, pair.Value });
                }
                ComUtil.Call(row, "setAttribute", new object[] { "editprop", "A" });
            }
            finally
            {
                ComUtil.Final(row);
            }
        }

        static object ChildRows(object data)
        {
            object doc = ComUtil.Get(data, "ownerDocument");
            try
            {
                return ComUtil.Call(doc, "selectNodes", new object[] { "//rs:data/z:row" });
            }
            finally
            {
                ComUtil.ReleaseOne(doc);
            }
        }
    }
}
