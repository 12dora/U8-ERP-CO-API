using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 不良品处理单 VO 的 domHead / domBody 改写（docs/u8-notes.md「不良品处理单」）。属性名一律按 voucheritems.FieldName
    // 原样（大写）直接 setAttribute，不按 schema 换大小写：U8 的 clsRejectDA.AddHead 按名字区分大小写取值，
    // 表头的 ISWFCONTROLLED 与 schema 里的 IsWfControlled 是两个属性，写错了 INSERT 报「不能将值 NULL 插入列 'IsWfControlled'」。
    internal static class QmRejDom
    {
        public const string Added = "A";
        static readonly string[] CloneClear = new string[] { "AUTOID", "IROWNO", "CBSYSBARCODE", "UFTS" };

        // 表头要写的字段（不含单号 CREJECTCODE，它在核对通过后才取号）。
        // 扩展自定义项 chdefine11–16 用 defineNames（模板字段名的原样大小写，QmRejUnits.Fill），没有登记的按小写原名。
        internal static List<string[]> HeadFields(QmRejSpec spec, QmRejAsk ask, bool wf, Dictionary<string, string> defineNames)
        {
            List<string[]> f = new List<string[]>();
            f.Add(Pair("editprop", Added));
            f.Add(Pair("IVTID", spec.Vt.ToString(CultureInfo.InvariantCulture)));
            f.Add(Pair("ISWFCONTROLLED", wf ? "1" : "0"));
            f.Add(Pair("IVERIFYSTATE", "0"));
            if (ask.Date.Length > 0)
            {
                f.Add(Pair("DDATE", ask.Date));
            }
            for (int n = 1; n <= 16; n++)
            {
                string key = "cdefine" + n.ToString(CultureInfo.InvariantCulture);
                string value;
                if (ask.Head.TryGetValue(key, out value) && value.Length > 0)
                {
                    f.Add(Pair(key.ToUpperInvariant(), value));
                }
            }
            ExtraDefines(f, ask, defineNames);
            return f;
        }

        static void ExtraDefines(List<string[]> f, QmRejAsk ask, Dictionary<string, string> defineNames)
        {
            for (int n = 11; n <= 16; n++)
            {
                string key = "chdefine" + n.ToString(CultureInfo.InvariantCulture);
                string value;
                string name;
                if (ask.Head.TryGetValue(key, out value) && value.Length > 0)
                {
                    f.Add(Pair(defineNames != null && defineNames.TryGetValue(key, out name) ? name : key, value));
                }
            }
        }

        // 一行表体要写的字段：数量与件数、部门、处理流程与处理方式、不良原因（U8 必输项核对 CREASONNAME）、存货（同表头）、
        // 降级时的处理后存货、数量（= 处理数量）、单位、换算率、件数，仓库。计量与部门的取法见 QmRejUnits。
        internal static List<string[]> LineFields(QmRejLine line, QmRejUnit unit)
        {
            List<string[]> f = new List<string[]>();
            f.Add(Pair("editprop", Added));
            f.Add(Pair("FQUANTITY", QmSql.Num(line.Qty)));
            f.Add(Pair("FNUM", QmRejUnits.Pieces(line.Qty, unit.Unit, unit.Rate, unit.NumDigits)));
            f.Add(Pair("CDEPCODE", unit.Dep));
            f.Add(Pair("IDISPOSEFLOW", line.Flow.ToString(CultureInfo.InvariantCulture)));
            f.Add(Pair("CSCRAPDISCODE", line.Dispose));
            f.Add(Pair("CSCRAPDISNAME", line.DisposeName));
            f.Add(Pair("CREASONCODE", line.Reason));
            f.Add(Pair("CREASONNAME", line.ReasonName));
            f.Add(Pair("CINVCODE", unit.Inv));
            if (line.Flow == QmRejSrc.DimFlow)
            {
                f.Add(Pair("CDIMINVCODE", line.DimInv));
                f.Add(Pair("FDIMQUANTITY", QmSql.Num(line.Qty)));
                string pieces = QmRejUnits.Pieces(line.Qty, line.DimUnit, line.DimRate, unit.NumDigits);
                if (pieces.Length > 0)
                {
                    f.Add(Pair("CDIMUNITID", line.DimUnit));
                    f.Add(Pair("FDIMCHANGRATE", QmSql.Num(line.DimRate)));
                    f.Add(Pair("FDIMNUM", pieces));
                }
            }
            if (line.Wh.Length > 0)
            {
                f.Add(Pair("CBWHCODE", line.Wh));
            }
            return f;
        }

        public static void FillHead(object dom, List<string[]> fields)
        {
            List<object> rows = DomRows.RowsOf(dom);
            try
            {
                if (rows.Count != 1)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 参照检验单后表头行数不是 1");
                }
                Write(rows[0], fields);
            }
            finally
            {
                Release(rows);
            }
        }

        // 表体：U8 参照后留下一行；多行处置时把这一行 cloneNode(true) 出其余各行（同一检验单，U8 留下的属性对各行相同）。
        public static void FillBody(object dom, QmRejAsk ask, QmRejUnit unit)
        {
            List<object> rows = DomRows.RowsOf(dom);
            try
            {
                if (rows.Count != 1)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 参照检验单后表体行数不是 1");
                }
                for (int i = 1; i < ask.Lines.Count; i++)
                {
                    rows.Add(CloneAfter(dom, rows[0]));
                }
                for (int i = 0; i < ask.Lines.Count; i++)
                {
                    Write(rows[i], LineFields(ask.Lines[i], unit));
                }
            }
            finally
            {
                Release(rows);
            }
        }

        // 复制 U8 留下的那一行（在写任何字段之前），并去掉行主键、行号、条码这类每行各自的列；件数等由 LineFields 每行重算。
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

        static string[] Pair(string name, string value)
        {
            return new string[] { name, value ?? "" };
        }
    }
}
