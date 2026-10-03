using System;
using System.Collections.Generic;

namespace U8Co
{
    // 质量单据 DOM 上的一行：只写该视图 schema 里有的字段（大小写照 schema），空值不写；editprop 和扩展自定义项
    // chdefine11–16 不在视图里，按原名直接写（实测 U8 认这两类属性）。
    internal sealed class QmRow : IDisposable
    {
        public object Dom;
        public object Row;
        readonly List<string> _schema;
        readonly HashSet<string> _names;

        QmRow(object dom, object row)
        {
            Dom = dom;
            Row = row;
            _schema = DomRows.Schema(dom);
            _names = new HashSet<string>(_schema, StringComparer.OrdinalIgnoreCase);
        }

        public static QmRow Add(object dom)
        {
            return new QmRow(dom, DomRows.AddRow(dom));
        }

        // 已有的第一行（取号后补单号用）。
        public static QmRow First(object dom)
        {
            List<object> rows = DomRows.RowsOf(dom);
            if (rows.Count == 0)
            {
                throw new BridgeException(500, "internal", "表头模板没有行");
            }
            for (int i = 1; i < rows.Count; i++)
            {
                ComUtil.ReleaseOne(rows[i]);
            }
            return new QmRow(dom, rows[0]);
        }

        public bool Has(string name)
        {
            return _names.Contains(name);
        }

        public void Put(string name, string value)
        {
            if (value == null || value.Length == 0 || !_names.Contains(name))
            {
                return;
            }
            DomRows.Set(Dom, Row, name, value, _schema);
        }

        public void Put(string name, decimal value)
        {
            Put(name, QmSql.Num(value));
        }

        public void Raw(string name, string value)
        {
            if (value == null || value.Length == 0)
            {
                return;
            }
            DomRows.Set(Dom, Row, name, value, _schema);
        }

        public void Dispose()
        {
            object row = Row;
            Row = null;
            ComUtil.ReleaseOne(row);
        }
    }

    internal static class QmDom
    {
        public const string Added = "A";

        // 新增用的空白：视图 where 1=2（经 TplCache，每次给新 DOM）。
        public static object Blank(object conn, string view)
        {
            return DomRows.Blank(conn, "select * from " + CoRows.Ident(view) + " where 1=2");
        }

        // 删除、审核、弃审用的 DOM：从同一视图按 ID 读出（空白 DOM 会被 U8 当成来源已被修改）。表体读不到行时给空白。
        public static object Loaded(object conn, string view, int id, bool head)
        {
            string sql = "select * from " + CoRows.Ident(view) + " where ID=?";
            try
            {
                return AdoXml.LoadDom(conn, sql, id);
            }
            catch (BridgeException ex)
            {
                if (head || ex.Status != 404)
                {
                    throw;
                }
                return Blank(conn, view);
            }
        }

        public static object[] LoadedPair(object conn, QmSpec spec, int id)
        {
            object[] doms = new object[2];
            try
            {
                doms[0] = Loaded(conn, spec.HeadView, id, true);
                doms[1] = Loaded(conn, spec.BodyView, id, false);
                return doms;
            }
            catch
            {
                Release(doms);
                throw;
            }
        }

        public static object[] BlankPair(object conn, QmSpec spec)
        {
            object[] doms = new object[2];
            try
            {
                doms[0] = Blank(conn, spec.HeadView);
                doms[1] = Blank(conn, spec.BodyView);
                return doms;
            }
            catch
            {
                Release(doms);
                throw;
            }
        }

        public static void Release(object[] doms)
        {
            if (doms == null)
            {
                return;
            }
            for (int i = doms.Length - 1; i >= 0; i--)
            {
                ComUtil.Final(doms[i]);
                doms[i] = null;
            }
        }

        // U8 保存后把新主键写回表头行的 ID；读不到为 0。
        public static int HeadId(object dom)
        {
            try
            {
                List<Dictionary<string, object>> rows = Rows.FromDom(dom, 1);
                return rows == null || rows.Count == 0 ? 0 : CoRows.AsId(CoRows.Col(rows[0], "ID"));
            }
            catch (Exception)
            {
                return 0;
            }
        }

        // 编号种子：填好的表头行（字段名照视图）。
        public static Dictionary<string, object> HeadSeeds(object dom)
        {
            List<Dictionary<string, object>> rows = Rows.FromDom(dom, 1);
            if (rows == null || rows.Count == 0)
            {
                throw new BridgeException(500, "internal", "表头模板没有行");
            }
            return rows[0];
        }

        public static string Now()
        {
            return DateTime.Now.ToString("HH:mm:ss", System.Globalization.CultureInfo.InvariantCulture);
        }
    }
}
