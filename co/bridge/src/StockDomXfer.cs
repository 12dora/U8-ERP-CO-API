using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 调拨单空模板。表头表体按 12 的视图 where 1=2。收发类别 cORdCode / cIRdCode 只用调用方给的，不补缺省（各账套编码不同）。
    internal static partial class StockDom
    {

        static object TransferHead(object conn, VoucherKind kind, Dictionary<string, object> fields, string maker, string billDate)
        {
            if (fields == null)
            {
                throw new BridgeException(400, "bad_request", "缺少表头");
            }
            object dom = Blank12(conn, true);
            try
            {
                List<string> names = DomRows.Schema(dom);
                RequireEdit(names);
                Dictionary<string, string> row = RowMap();
                FillTransfer(names, row, maker);
                Overlay(names, row, fields, kind, "head");
                FillTransfer(names, row, maker);
                PutMissing(names, row, "dTVDate", billDate ?? "");
                if (CellOf(row, "cOWhCode").Length == 0)
                {
                    throw new BridgeException(400, "bad_request", "必须指定转出仓库");
                }
                if (CellOf(row, "cIWhCode").Length == 0)
                {
                    throw new BridgeException(400, "bad_request", "必须指定转入仓库");
                }
                if (CellOf(row, "dTVDate").Length == 0)
                {
                    throw new BridgeException(400, "bad_request", "单据日期必须是 yyyy-MM-dd");
                }
                Stamp(dom, DomRows.AddRow(dom), row);
                object keep = dom;
                dom = null;
                return keep;
            }
            finally
            {
                ComUtil.Final(dom);
            }
        }

        static object TransferBody(object conn, VoucherKind kind, object[] lines)
        {
            if (lines == null || lines.Length < 1 || lines.Length > 200)
            {
                throw new BridgeException(400, "bad_request", "表体须为 1 到 200 行");
            }
            object dom = Blank12(conn, false);
            try
            {
                List<string> names = DomRows.Schema(dom);
                RequireEdit(names);
                for (int i = 0; i < lines.Length; i++)
                {
                    TransferLine(conn, dom, names, kind, lines[i], i + 1);
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

        static void TransferLine(object conn, object dom, List<string> names, VoucherKind kind, object raw, int rowNo)
        {
            Dictionary<string, object> line = raw as Dictionary<string, object>;
            if (line == null)
            {
                throw new BridgeException(400, "bad_request", "表体行不是对象");
            }
            Dictionary<string, string> row = RowMap();
            Put(names, row, "editprop", "A");
            Put(names, row, "bcosting", "1");
            Put(names, row, "irowno", rowNo.ToString(CultureInfo.InvariantCulture));
            Overlay(names, row, line, kind, "lines");
            Put(names, row, "editprop", "A");
            if (CellOf(row, "cInvCode").Length == 0)
            {
                throw new BridgeException(400, "bad_request", "表体行缺少存货");
            }
            decimal qty;
            if (!StockUnits.Dec(CellOf(row, "iTVQuantity"), out qty) || qty <= 0m)
            {
                throw new BridgeException(400, "bad_request", "数量必须大于 0");
            }
            StockUnits.Apply(conn, names, row, "iTVQuantity", "iTVNum", true);
            Stamp(dom, DomRows.AddRow(dom), row);
        }

        static void FillTransfer(List<string> names, Dictionary<string, string> row, string maker)
        {
            Put(names, row, "editprop", "A");
            Put(names, row, "vt_id", "89");
            Put(names, row, "csource", "1");
            Put(names, row, "itransflag", "正向");
            Put(names, row, "iverifystate", "0");
            Put(names, row, "iswfcontrolled", "0");
            Put(names, row, "cmaker", maker ?? "");
            Put(names, row, "id", "");
            Put(names, row, "ctvcode", "");
        }

        static object Blank12(object conn, bool head)
        {
            string view = ViewName("12", head);
            string alias = head ? "m" : "d";
            string sql = "select 'A' as editprop, " + alias + ".* from dbo." + view
                + " " + alias + " with(nolock) where 1=2";
            object dom = DomRows.Blank(conn, sql);
            if (dom == null)
            {
                throw new BridgeException(503, "com_unavailable", "MSXML 未注册");
            }
            return dom;
        }

        static void Stamp(object dom, object node, Dictionary<string, string> row)
        {
            try
            {
                foreach (KeyValuePair<string, string> kv in row)
                {
                    if (kv.Value == null)
                    {
                        continue;
                    }
                    DomRows.Set(dom, node, kv.Key, kv.Value);
                }
            }
            finally
            {
                ComUtil.ReleaseOne(node);
            }
        }

        static void PutMissing(List<string> names, Dictionary<string, string> row, string key, string value)
        {
            if (value == null || value.Trim().Length == 0)
            {
                return;
            }
            string canon = Canonical(names, key);
            if (canon == null || CellOf(row, canon).Length > 0)
            {
                return;
            }
            row[canon] = value.Trim();
        }

        static string Alias(List<string> names, VoucherKind kind, string key)
        {
            if (kind == null || kind.StType != "12" || key == null)
            {
                return key;
            }
            string low = key.ToLowerInvariant();
            if (low == "cmemo")
            {
                return Canonical(names, "cMemo") != null ? "cMemo" : "cTVMemo";
            }
            if (low == "cbatch")
            {
                return Canonical(names, "cBatch") != null ? "cBatch" : "cTVBatch";
            }
            return key;
        }

        static string CellOf(Dictionary<string, string> row, string name)
        {
            if (row == null || name == null)
            {
                return "";
            }
            string value;
            if (!row.TryGetValue(name, out value) || value == null)
            {
                return "";
            }
            return value.Trim();
        }

    }
}
