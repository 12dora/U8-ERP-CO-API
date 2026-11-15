using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 其他报检单、其他检验单 VO 的 domHead / domBody 改写。同不良品处理单（QmRejDom）：属性名照 voucheritems.FieldName
    // 的原样（大写）直接 setAttribute，不按 schema 换大小写（VO 接口的数据层按名字区分大小写取值，见 docs/u8-notes.md
    // 「不良品处理单」的 ISWFCONTROLLED）；扩展自定义项 chdefine11–16 照模板字段名的原样大小写（DefineNames）。
    internal static class QmOthDom
    {
        public const string Added = "A";
        static readonly string[] CloneClear = new string[] { "AUTOID", "IROWNO", "CBSYSBARCODE", "UFTS" };
        const string FieldSql = "select top 1 FieldName from voucheritems where VT_ID=? and CardSection='T' and lower(FieldName)=?";

        // 空白表头、表体各挂一行（视图 where 1=2）。视图 <名>B 是表头、<名>T 是表体（同 QmSpec）；按 schema 再核一次：
        // 只有一边有 AUTOID（表体行主键）时以它为表体。
        public static object[] BlankPair(object conn, QmOthSpec spec)
        {
            object[] doms = new object[2];
            try
            {
                doms[0] = QmDom.Blank(conn, spec.HeadView);
                doms[1] = QmDom.Blank(conn, spec.BodyView);
                if (HasAutoId(doms[0]) && !HasAutoId(doms[1]))
                {
                    object t = doms[0];
                    doms[0] = doms[1];
                    doms[1] = t;
                }
                ComUtil.ReleaseOne(DomRows.AddRow(doms[0]));
                ComUtil.ReleaseOne(DomRows.AddRow(doms[1]));
                return doms;
            }
            catch
            {
                QmDom.Release(doms);
                throw;
            }
        }

        static bool HasAutoId(object dom)
        {
            List<string> schema = DomRows.Schema(dom);
            for (int i = 0; i < schema.Count; i++)
            {
                if (QmOthSpec.Same(schema[i], "AUTOID"))
                {
                    return true;
                }
            }
            return false;
        }

        // 请求里 chdefine11–16 的模板字段名（原样大小写）；模板没有这一项时用小写原名。
        public static Dictionary<string, string> DefineNames(object conn, int vt, ICollection<string> keys)
        {
            Dictionary<string, string> names = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (string key in keys)
            {
                if (key.StartsWith("chdefine", StringComparison.Ordinal))
                {
                    string name = QmSql.Scalar(conn, FieldSql, vt, key);
                    names[key] = name.Length > 0 ? name : key;
                }
            }
            return names;
        }

        // 自定义项：cdefine1–16 写大写列名，chdefine11–16 写模板字段名。
        public static void Defines(List<string[]> f, IDictionary<string, string> head, Dictionary<string, string> names)
        {
            foreach (KeyValuePair<string, string> kv in head)
            {
                string name;
                if (kv.Key.StartsWith("chdefine", StringComparison.Ordinal))
                {
                    f.Add(Pair(names != null && names.TryGetValue(kv.Key, out name) ? name : kv.Key, kv.Value));
                }
                else if (kv.Key.StartsWith("cdefine", StringComparison.Ordinal))
                {
                    f.Add(Pair(kv.Key.ToUpperInvariant(), kv.Value));
                }
            }
        }

        // 表头只有一行（AddNewVoucher 之后 U8 留下的那一行）。
        public static void FillHead(object dom, List<string[]> fields)
        {
            List<object> rows = DomRows.RowsOf(dom);
            try
            {
                if (rows.Count != 1)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 返回的表头行数不是 1");
                }
                Write(rows[0], fields);
            }
            finally
            {
                Release(rows);
            }
        }

        // 表体：保留 U8 留下的第一行，删掉其余行，再由它 cloneNode(true) 出其余各行（去掉行主键、行号、条码、ufts），逐行写。
        public static void FillBody(object dom, List<List<string[]>> lines)
        {
            List<object> rows = DomRows.RowsOf(dom);
            try
            {
                if (rows.Count == 0)
                {
                    rows.Add(DomRows.AddRow(dom));
                }
                Trim(rows);
                for (int i = 1; i < lines.Count; i++)
                {
                    rows.Add(CloneAfter(dom, rows[0]));
                }
                for (int i = 0; i < lines.Count; i++)
                {
                    Write(rows[i], lines[i]);
                }
            }
            finally
            {
                Release(rows);
            }
        }

        static void Trim(List<object> rows)
        {
            while (rows.Count > 1)
            {
                object row = rows[rows.Count - 1];
                object parent = ComUtil.Get(row, "parentNode");
                try
                {
                    ComUtil.Call(parent, "removeChild", new object[] { row });
                }
                finally
                {
                    ComUtil.ReleaseOne(parent);
                    ComUtil.ReleaseOne(row);
                }
                rows.RemoveAt(rows.Count - 1);
            }
        }

        static object CloneAfter(object dom, object row)
        {
            object parent = ComUtil.Get(row, "parentNode");
            try
            {
                object copy = ComUtil.Call(row, "cloneNode", new object[] { true });
                ComUtil.Call(parent, "appendChild", new object[] { copy });
                for (int i = 0; i < CloneClear.Length; i++)
                {
                    DomRows.Set(dom, copy, CloneClear[i], null);
                }
                return copy;
            }
            finally
            {
                ComUtil.ReleaseOne(parent);
            }
        }

        // 空值不写。
        static void Write(object row, List<string[]> fields)
        {
            for (int i = 0; i < fields.Count; i++)
            {
                if (fields[i][1] != null && fields[i][1].Length > 0)
                {
                    ComUtil.Call(row, "setAttribute", new object[] { fields[i][0], fields[i][1] });
                }
            }
        }

        static void Release(List<object> rows)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                ComUtil.ReleaseOne(rows[i]);
            }
        }

        internal static string[] Pair(string name, string value)
        {
            return new string[] { name, value ?? "" };
        }

        internal static string[] Pair(string name, decimal value)
        {
            return new string[] { name, QmSql.Num(value) };
        }

        internal static string Int(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }
}
