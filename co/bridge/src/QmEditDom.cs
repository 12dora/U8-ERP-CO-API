using System;
using System.Collections.Generic;

namespace U8Co
{
    // 质量单据修改：一次请求的上下文（QmEdit / QmOthEdit 共用）。Fields 是要写进表头的 {列名, 值, 请求字段, 派生标记}，
    // 同时是保存后回读核对的期望值；派生列（检验员姓名、件数）视图里没有就不写，也不参与核对。
    internal sealed class QmEditJob
    {
        public WorkContext Ctx;
        public QmEditAsk Ask;
        public int Id;
        public string VouchType = "";
        public int Vt;
        public string HeadTable = "";
        public string Title = "";
        public string Component = "";
        // 修改前的表头（select *，含 UFTS）；检验单另有扩展自定义项行和报检单行的累计检验数量 / 检验标记。
        public Dictionary<string, object> Doc;
        public Dictionary<string, object> Extra;
        public string LineState = "";
        public Dictionary<string, string> DefineNames;
        public readonly List<string[]> Fields = new List<string[]>();

        public string Ufts
        {
            get { return CoRows.Col(Doc, "UFTS"); }
        }

        public const string DerivedMark = "derived";

        public void Add(string column, string value, string field)
        {
            Fields.Add(new string[] { column, value ?? "", field, "" });
        }

        public void AddDerived(string column, string value, string field)
        {
            Fields.Add(new string[] { column, value ?? "", field, DerivedMark });
        }
    }

    // 把修改写进 U8 的 DOM。检验单（QM03 / QM04）是从视图按 ID 读出的 DOM：属性名照视图 schema 的大小写（DomRows.Set），
    // 视图里没有的列 400；扩展自定义项 chdefine11–16 不在视图里，按模板字段名写，并且要把单据上已有的值原样写回去
    // （视图不带这几列，U8 按模板核对必输，缺了报「'复核人编码'不能为空！」）。其他报检单、其他检验单是 VO 的 domHead / domBody：
    // 属性名一律大写直接 setAttribute（同 QmOthDom），chdefine 照模板字段名。表头 editprop=M；检验项目只在改过的行上写 editprop=M。
    internal static class QmEditDom
    {
        public const string Modified = "M";
        static readonly string[] Extras = new string[] { "chdefine11", "chdefine12", "chdefine13", "chdefine14", "chdefine15", "chdefine16" };

        public static void Apply(QmEditJob job, object head, object body, bool vo)
        {
            List<object> rows = DomRows.RowsOf(head);
            try
            {
                if (rows.Count == 0)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 返回的表头没有行");
                }
                List<string> schema = vo ? null : DomRows.Schema(head);
                if (!vo)
                {
                    CopyExtras(job, head, rows[0], schema);
                }
                for (int i = 0; i < job.Fields.Count; i++)
                {
                    Put(head, rows[0], schema, job.Fields[i]);
                }
                Write(head, rows[0], schema, "editprop", Modified);
            }
            finally
            {
                Release(rows);
            }
            if (job.Ask.Items != null)
            {
                QmEditItems.Apply(job, body, vo);
            }
        }

        // 单据上已有的扩展自定义项（请求没改的）原样写进表头。
        static void CopyExtras(QmEditJob job, object dom, object row, List<string> schema)
        {
            for (int i = 0; i < Extras.Length; i++)
            {
                string value = CoRows.Col(job.Extra, Extras[i]);
                if (value.Length == 0 || job.Ask.Has(Extras[i]))
                {
                    continue;
                }
                Write(dom, row, schema, DefineName(job, Extras[i]), value);
            }
        }

        static void Put(object dom, object row, List<string> schema, string[] field)
        {
            string column = field[0];
            bool extra = column.StartsWith("chdefine", StringComparison.OrdinalIgnoreCase);
            if (schema != null && !extra && !Listed(schema, column))
            {
                if (field[3] == QmEditJob.DerivedMark)
                {
                    return;
                }
                throw BridgeException.BadField("head." + field[2], "U8 单据视图没有字段 " + column + "，不能修改 " + field[2]);
            }
            Write(dom, row, schema, column, field[1]);
        }

        // schema 为 null 表示 VO 的 DOM：直接 setAttribute（列名已是大写或模板字段名）。
        internal static void Write(object dom, object row, List<string> schema, string name, string value)
        {
            if (schema == null)
            {
                ComUtil.Call(row, "setAttribute", new object[] { name, value ?? "" });
                return;
            }
            DomRows.Set(dom, row, name, value ?? "", schema);
        }

        internal static bool Listed(List<string> schema, string name)
        {
            for (int i = 0; i < schema.Count; i++)
            {
                if (string.Equals(schema[i], name, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        // chdefine11–16 的模板字段名（QmOthDom.DefineNames 读出），没有时用小写原名。
        internal static string DefineName(QmEditJob job, string low)
        {
            string name;
            return job.DefineNames != null && job.DefineNames.TryGetValue(low, out name) && name.Length > 0 ? name : low;
        }

        internal static void Release(List<object> rows)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                ComUtil.ReleaseOne(rows[i]);
            }
        }

        internal static string[] ExtraNames()
        {
            return (string[])Extras.Clone();
        }
    }
}
