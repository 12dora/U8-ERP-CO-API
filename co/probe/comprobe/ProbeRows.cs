using System;
using System.Collections;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace U8Co
{
    internal static class ProbeRows
    {
        public static void Add(ProbeRun run, Dictionary<string, object> step)
        {
            object row = AddRow(run.NeedRef(StepIo.Req(step, "dom")));
            Line line = ProbeOut.Begin(run, step, true);
            ProbeOut.Keep(run, step, row, line);
            ProbeOut.Emit(line);
        }

        public static void Show(ProbeRun run, Dictionary<string, object> step)
        {
            object dom = run.NeedRef(StepIo.Req(step, "dom"));
            List<object> nodes = RowsOf(dom);
            int take = TakeCount(nodes.Count, ProbeDomLimit(step));
            List<Dictionary<string, object>> table = new List<Dictionary<string, object>>();
            for (int i = 0; i < take; i++)
            {
                table.Add(Read(nodes[i], ColsOf(step)));
            }
            Line line = ProbeOut.Begin(run, step, true);
            line.Num("count", nodes.Count);
            line.Raw("rows", ProbeOut.Rows(table));
            ProbeOut.Emit(line);
        }

        public static void Set(ProbeRun run, Dictionary<string, object> step)
        {
            if (!StepIo.Present(step, "value"))
            {
                throw new BridgeException(400, "bad_request", "缺少 value");
            }
            object dom = run.NeedRef(StepIo.Req(step, "dom"));
            object row = RowAt(run, dom, step["row"]);
            string attr = StepIo.Req(step, "attr");
            if (step["value"] == null)
            {
                ProbeAttr.Remove(row, attr);
            }
            else
            {
                ProbeAttr.Write(dom, row, attr, ScalarText(run, step["value"]));
            }
            ProbeOut.Emit(ProbeOut.Begin(run, step, true));
        }

        public static void Single(ProbeRun run, Dictionary<string, object> step)
        {
            if (!StepIo.Has(step, "var"))
            {
                throw new BridgeException(400, "bad_request", "需要变量");
            }
            object dom = run.NeedRef(StepIo.Req(step, "dom"));
            object clone = CopyOne(dom, RowIndex(run, dom, step["row"]));
            Line line = ProbeOut.Begin(run, step, true);
            ProbeOut.Keep(run, step, clone, line);
            ProbeOut.Emit(line);
        }

        static object AddRow(object dom)
        {
            if (dom == null)
            {
                throw new BridgeException(500, "internal", "DOM 为空");
            }
            ProbeDom.UseXPath(dom);
            object data = DataOf(dom);
            object row = null;
            try
            {
                row = ComUtil.Call(dom, "createNode", new object[] { 1, "z:row", ProbeDom.RowNs });
                ComUtil.Call(data, "appendChild", new object[] { row });
                object keep = row;
                row = null;
                return keep;
            }
            finally
            {
                ComUtil.ReleaseOne(data);
                ComUtil.Final(row);
            }
        }

        static object DataOf(object dom)
        {
            object found = ComUtil.Call(dom, "selectSingleNode", new object[] { "//rs:data" });
            if (found != null && !(found is DBNull))
            {
                return found;
            }
            ComUtil.ReleaseOne(found);
            return CreateData(dom);
        }

        static object CreateData(object dom)
        {
            object data = ComUtil.Call(dom, "createNode", new object[] { 1, "rs:data", ProbeDom.RsNs });
            object root = ComUtil.Get(dom, "documentElement");
            try
            {
                if (root == null || root is DBNull)
                {
                    throw new BridgeException(500, "internal", "模板没有字段");
                }
                ComUtil.Call(root, "appendChild", new object[] { data });
                return data;
            }
            finally
            {
                ComUtil.ReleaseOne(root);
            }
        }

        static List<object> RowsOf(object dom)
        {
            ProbeDom.UseXPath(dom);
            object list = ComUtil.Call(dom, "selectNodes", new object[] { "//rs:data/z:row" });
            try
            {
                return Take(list);
            }
            finally
            {
                ComUtil.ReleaseOne(list);
            }
        }

        static List<object> Take(object list)
        {
            List<object> rows = new List<object>();
            int count = Len(list);
            for (int i = 0; i < count; i++)
            {
                rows.Add(ComUtil.Call(list, "item", new object[] { i }));
            }
            return rows;
        }

        static Dictionary<string, object> Read(object row, string cols)
        {
            if (cols == "*")
            {
                return All(row);
            }
            Dictionary<string, object> map = new Dictionary<string, object>();
            string[] parts = cols.Split(',');
            for (int i = 0; i < parts.Length; i++)
            {
                string name = parts[i].Trim();
                if (name.Length > 0)
                {
                    map[name] = ProbeAttr.Get(row, name);
                }
            }
            return map;
        }

        static Dictionary<string, object> All(object row)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            object attrs = ComUtil.Get(row, "attributes");
            try
            {
                int count = Len(attrs);
                for (int i = 0; i < count; i++)
                {
                    PutAttr(map, attrs, i);
                }
                return map;
            }
            finally
            {
                ComUtil.ReleaseOne(attrs);
            }
        }

        static void PutAttr(Dictionary<string, object> map, object attrs, int index)
        {
            object attr = ComUtil.Call(attrs, "item", new object[] { index });
            try
            {
                string name = ProbeAttr.Name(attr);
                if (name.Length > 0 && !ProbeAttr.Skip(name))
                {
                    map[name] = Values.Text(ComUtil.Get(attr, "text"));
                }
            }
            finally
            {
                ComUtil.ReleaseOne(attr);
            }
        }

        static object RowAt(ProbeRun run, object dom, object spec)
        {
            int index = RowIndex(run, dom, spec);
            List<object> rows = RowsOf(dom);
            return rows[index];
        }

        static int RowIndex(ProbeRun run, object dom, object spec)
        {
            string text = spec as string;
            if (text != null && text.Length > 0 && text[0] == '$')
            {
                return IndexOf(dom, run.NeedRef(text));
            }
            if (spec == null)
            {
                throw new BridgeException(400, "bad_request", "缺少 row");
            }
            int index = Nums.AsInt(spec);
            int count = RowsOf(dom).Count;
            if (index < 0 || index >= count)
            {
                throw new BridgeException(400, "bad_request", "明细行不在单据中");
            }
            return index;
        }

        static int IndexOf(object dom, object row)
        {
            ProbeDom.UseXPath(dom);
            object list = ComUtil.Call(dom, "selectNodes", new object[] { "//rs:data/z:row" });
            try
            {
                return Find(list, row);
            }
            finally
            {
                ComUtil.ReleaseOne(list);
            }
        }

        static int Find(object list, object row)
        {
            int count = Len(list);
            string want = Values.Text(ComUtil.Get(row, "xml"));
            int hit = -1;
            for (int i = 0; i < count; i++)
            {
                if (SameRow(list, row, i))
                {
                    return i;
                }
                if (XmlRow(list, i, want))
                {
                    hit = i;
                }
            }
            if (hit < 0)
            {
                throw new BridgeException(400, "bad_request", "明细行不在单据中");
            }
            return hit;
        }

        static bool SameRow(object list, object row, int index)
        {
            object node = ComUtil.Call(list, "item", new object[] { index });
            if (object.ReferenceEquals(node, row))
            {
                return true;
            }
            ComUtil.ReleaseOne(node);
            return false;
        }

        static bool XmlRow(object list, int index, string want)
        {
            object node = ComUtil.Call(list, "item", new object[] { index });
            try
            {
                return Values.Text(ComUtil.Get(node, "xml")) == want;
            }
            finally
            {
                ComUtil.ReleaseOne(node);
            }
        }

        static object CopyOne(object dom, int keep)
        {
            object clone = ComUtil.Call(dom, "cloneNode", new object[] { true });
            try
            {
                ProbeDom.UseXPath(clone);
                Drop(clone, keep);
                return clone;
            }
            catch
            {
                ComUtil.Final(clone);
                throw;
            }
        }

        static void Drop(object dom, int keep)
        {
            List<object> nodes = RowsOf(dom);
            if (keep < 0 || keep >= nodes.Count)
            {
                throw new BridgeException(400, "bad_request", "明细行不在单据中");
            }
            for (int i = 0; i < nodes.Count; i++)
            {
                if (i != keep)
                {
                    RemoveRow(nodes[i]);
                }
            }
        }

        static void RemoveRow(object row)
        {
            object parent = ComUtil.Get(row, "parentNode");
            try
            {
                if (parent != null && !(parent is DBNull))
                {
                    ComUtil.Call(parent, "removeChild", new object[] { row });
                }
            }
            finally
            {
                ComUtil.ReleaseOne(parent);
            }
        }

        static string ScalarText(ProbeRun run, object raw)
        {
            object value = ProbeCall.Arg(run, raw);
            if (value == null)
            {
                return null;
            }
            if (value is bool)
            {
                return (bool)value ? "1" : "0";
            }
            if (value is string)
            {
                return (string)value;
            }
            if (Marshal.IsComObject(value))
            {
                throw new BridgeException(400, "bad_request", "属性值不是标量");
            }
            return Nums.Invariant(value);
        }

        static string ColsOf(Dictionary<string, object> step)
        {
            if (!StepIo.Present(step, "cols") || step["cols"] == null)
            {
                return "*";
            }
            string text = step["cols"] as string;
            if (text != null)
            {
                return text;
            }
            ArrayList list = StepIo.AsList(step["cols"]);
            if (list == null)
            {
                throw new BridgeException(400, "bad_request", "cols 无效");
            }
            return Join(list);
        }

        static string Join(ArrayList list)
        {
            string text = "";
            for (int i = 0; i < list.Count; i++)
            {
                if (i > 0)
                {
                    text = text + ",";
                }
                text = text + Convert.ToString(list[i]);
            }
            return text;
        }

        static int Len(object list)
        {
            if (list == null || list is DBNull)
            {
                return 0;
            }
            return Convert.ToInt32(ComUtil.Get(list, "length"));
        }

        static int TakeCount(int count, int max)
        {
            if (count < max)
            {
                return count;
            }
            return max;
        }

        static int ProbeDomLimit(Dictionary<string, object> step)
        {
            if (!StepIo.Present(step, "max") || step["max"] == null)
            {
                return 50;
            }
            int max = Nums.AsInt(step["max"]);
            if (max < 1 || max > 200)
            {
                throw new BridgeException(400, "bad_request", "max 无效");
            }
            return max;
        }
    }
}
