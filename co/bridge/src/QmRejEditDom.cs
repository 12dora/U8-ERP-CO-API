using System.Collections.Generic;

namespace U8Co
{
    // 不良品处理单修改写进 VO 的 domHead / domBody（属性名照模板字段名直接 setAttribute，同 QmRejDom）。
    // 表头 editprop=M；修改行 editprop=M、删除行 editprop=D、新增行复制单据的第一行（在改任何行之前，去掉行主键、行号、条码）
    // 后 editprop=A。写过的行上，本次不写的处理后存货 / 数量 / 单位 / 件数、件数、仓库列去掉；新增行另去掉已处理标记与返工数量。
    internal static class QmRejEditDom
    {
        public const string Modified = "M";
        public const string Deleted = "D";
        // 写过的行上要么由 LineFields 重写、要么去掉的列。
        static readonly string[] Managed = new string[]
        {
            "FNUM", "CDIMINVCODE", "FDIMQUANTITY", "CDIMUNITID", "FDIMCHANGRATE", "FDIMNUM", "CBWHCODE"
        };
        // 处理后存货换了或不再降级时一并去掉的处理后批次、效期列。
        static readonly string[] DimExtra = new string[]
        {
            "CDIMBATCH", "DDIMVDATE", "IDIMMASSDATE", "CDIMMASSUNIT", "iDIMExpiratDateCalcu", "cDIMExpirationdate", "dDIMExpirationdate"
        };
        static readonly string[] AddedClear = new string[] { "BFLAG", "FSUMREWORKQTY", "FSUMREWORKNUM" };

        public static void Apply(QmRejEditJob e, object head, object body)
        {
            Head(e, head);
            if (e.Touched.Count > 0 || e.Deleted.Count > 0)
            {
                Body(e, body);
            }
        }

        static void Head(QmRejEditJob e, object head)
        {
            List<object> rows = DomRows.RowsOf(head);
            try
            {
                if (rows.Count != 1)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 载入的表头行数不是 1");
                }
                for (int i = 0; i < e.HeadFields.Count; i++)
                {
                    ComUtil.Call(rows[0], "setAttribute", new object[] { e.HeadFields[i][0], e.HeadFields[i][1] });
                }
                ComUtil.Call(rows[0], "setAttribute", new object[] { "editprop", Modified });
            }
            finally
            {
                QmEditDom.Release(rows);
            }
        }

        static void Body(QmRejEditJob e, object body)
        {
            List<object> rows = DomRows.RowsOf(body);
            List<object> made = new List<object>();
            try
            {
                if (rows.Count == 0)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 载入的表体没有行");
                }
                for (int i = 0; i < e.Touched.Count; i++)
                {
                    if (e.Touched[i].AutoId == 0)
                    {
                        made.Add(QmRejDom.CloneAfter(body, rows[0]));
                    }
                }
                for (int i = 0; i < e.Deleted.Count; i++)
                {
                    ComUtil.Call(Find(rows, e.Deleted[i]), "setAttribute", new object[] { "editprop", Deleted });
                }
                int next = 0;
                for (int i = 0; i < e.Touched.Count; i++)
                {
                    QmRejLine line = e.Touched[i];
                    bool added = line.AutoId == 0;
                    object row = added ? made[next++] : Find(rows, line.AutoId);
                    Write(e, body, row, line, added);
                }
            }
            finally
            {
                QmEditDom.Release(made);
                QmEditDom.Release(rows);
            }
        }

        static void Write(QmRejEditJob e, object body, object row, QmRejLine line, bool added)
        {
            string oldDim = added ? "" : DomRows.Get(row, "CDIMINVCODE").Trim();
            List<string[]> fields = QmRejDom.LineFields(line, e.Job.Unit);
            if (!added)
            {
                fields[0][1] = Modified;
            }
            QmRejDom.Write(row, fields);
            List<string> clear = new List<string>();
            for (int i = 0; i < Managed.Length; i++)
            {
                if (!Written(fields, Managed[i]))
                {
                    clear.Add(Managed[i]);
                }
            }
            if (line.Flow != QmRejSrc.DimFlow || !QmRejSpec.Same(oldDim, line.DimInv))
            {
                clear.AddRange(DimExtra);
            }
            if (added)
            {
                clear.AddRange(AddedClear);
            }
            for (int i = 0; i < clear.Count; i++)
            {
                DomRows.Set(body, row, clear[i], null);
            }
        }

        static bool Written(List<string[]> fields, string name)
        {
            for (int i = 0; i < fields.Count; i++)
            {
                if (QmRejSpec.Same(fields[i][0], name) && fields[i][1].Length > 0)
                {
                    return true;
                }
            }
            return false;
        }

        // 按 AUTOID 找 U8 载入的表体行；对不上（单据在闸门之后被改过）409。
        static object Find(List<object> rows, int autoId)
        {
            string want = autoId.ToString(System.Globalization.CultureInfo.InvariantCulture);
            for (int i = 0; i < rows.Count; i++)
            {
                if (DomRows.Get(rows[i], "AUTOID").Trim() == want)
                {
                    return rows[i];
                }
            }
            throw new BridgeException(409, "u8_rejected", "U8 载入的单据没有表体行 " + want + "，单据可能刚被修改，请重试");
        }
    }
}
