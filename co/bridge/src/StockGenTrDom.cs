using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 调拨单参照调拨申请单（StockGenTr）的模板：同调拨单新增用 transm / TransD 的 where 1=2（Blank12、FillTransfer）。
    // 表头仓库取自申请单（调用方不能改），部门、业务员、收发类别申请单上有就带过来，调用方可覆盖；ctranrequestcode 写申请单号。
    // 收发类别不补缺省（各账套编码不同）：申请单和调用方都没给时由 U8 自己报错。
    // 表体行由 StockGen 按申请单行给出（itrids、存货、批号、自由项、保质期、辅计量），数量由调用方给，件数按换算率重算。
    internal static partial class StockDom
    {
        static readonly string[] TrReqCols = new string[] { "cODepCode", "cIDepCode", "cPersonCode", "cORdCode", "cIRdCode" };

        internal static object TrGenHead(object conn, VoucherKind kind, Dictionary<string, object> req,
            Dictionary<string, object> fields, string maker, string billDate)
        {
            object dom = Blank12(conn, true);
            try
            {
                List<string> names = DomRows.Schema(dom);
                RequireEdit(names);
                Dictionary<string, string> row = RowMap();
                FillTransfer(names, row, maker);
                Overlay(names, row, fields, kind, "head");
                FillTransfer(names, row, maker);
                Put(names, row, "cOWhCode", StockMsg.Col(req, "cOWhCode"));
                Put(names, row, "cIWhCode", StockMsg.Col(req, "cIWhCode"));
                Put(names, row, "cTranRequestCode", StockMsg.Col(req, "cTVCode"));
                for (int i = 0; i < TrReqCols.Length; i++)
                {
                    PutMissing(names, row, TrReqCols[i], StockMsg.Col(req, TrReqCols[i]));
                }
                PutMissing(names, row, "dTVDate", billDate ?? "");
                if (CellOf(row, "cOWhCode").Length == 0 || CellOf(row, "cIWhCode").Length == 0)
                {
                    throw new BridgeException(409, "state_mismatch", "调拨申请单缺少转出或转入仓库");
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

        internal static object TrGenBody(object conn, List<Dictionary<string, string>> cells)
        {
            object dom = Blank12(conn, false);
            try
            {
                List<string> names = DomRows.Schema(dom);
                RequireEdit(names);
                for (int i = 0; i < cells.Count; i++)
                {
                    Dictionary<string, string> row = RowMap();
                    Put(names, row, "editprop", "A");
                    Put(names, row, "bcosting", "1");
                    Put(names, row, "irowno", (i + 1).ToString(CultureInfo.InvariantCulture));
                    foreach (KeyValuePair<string, string> kv in cells[i])
                    {
                        Put(names, row, kv.Key, kv.Value);
                    }
                    StockUnits.Apply(conn, names, row, "iTVQuantity", "iTVNum", true);
                    Stamp(dom, DomRows.AddRow(dom), row);
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
    }
}
