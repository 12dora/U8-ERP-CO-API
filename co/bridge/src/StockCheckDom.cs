using System;
using System.Collections.Generic;

namespace U8Co
{
    // 盘点单（18）新增的账面数量和 U8 的两道新增闸门。
    // 账面数量 iCVQuantity 调用方没给时按现存量 CurrentStock 取：仓库 + 存货 + 批号 + 自由项 1–10 逐项相等（没给的按空串比），
    // 与盘点行的键一致；货位、代管商不分（行上不收）。U8 界面用 VoucherCO.RefCheckInventory* 带出，这里不调（参数未实测）。
    // 盈亏由 U8 按实盘减账面计算，审核时生成其他出入库单。
    internal static partial class StockDom
    {
        const string BookSql = "select convert(varchar(40), isnull(sum(iQuantity), 0)) from CurrentStock"
            + " where cWhCode=? and cInvCode=? and isnull(cBatch, N'')=?";
        const string OpenCheckSql = "select top 1 convert(varchar(20), ID) from CheckVouch"
            + " where nullif(ltrim(rtrim(cAccounter)), N'') is null and cWhCode=?";

        static void FillBook(object conn, List<string> names, Dictionary<string, string> row, string wh)
        {
            string canon = Canonical(names, "iCVQuantity");
            if (canon == null || CellOf(row, canon).Length > 0)
            {
                return;
            }
            string sql = BookSql;
            List<object> args = new List<object>();
            args.Add(wh ?? "");
            args.Add(CellOf(row, "cInvCode"));
            args.Add(CellOf(row, "cCVBatch"));
            for (int i = 1; i <= 10; i++)
            {
                string n = i.ToString(System.Globalization.CultureInfo.InvariantCulture);
                sql = sql + " and isnull(cFree" + n + ", N'')=?";
                args.Add(CellOf(row, "cFree" + n));
            }
            string book = Rows.Scalar(conn, sql, args.ToArray());
            row[canon] = book == null || book.Trim().Length == 0 ? "0" : book.Trim();
        }

        // 盘点行的键：存货 + 批号 + 自由项 1–10（重复判断和账面数量共用）。
        internal static string CheckKey(Dictionary<string, string> row)
        {
            string key = CellOf(row, "cInvCode") + "\u0001" + CellOf(row, "cCVBatch");
            for (int i = 1; i <= 10; i++)
            {
                key = key + "\u0001" + CellOf(row, "cFree" + i.ToString(System.Globalization.CultureInfo.InvariantCulture));
            }
            return key;
        }

        // 同 U8 界面：盘盈 = 实盘 − 账面（大于 0 时），盘亏 = 账面 − 实盘（大于 0 时），另一边写 0。数量、件数各算一次；
        // 实盘或账面缺一个（例如没有辅计量时的件数）就不写。
        static void FillGainLoss(List<string> names, Dictionary<string, string> row, string real, string book,
            string gain, string loss)
        {
            decimal actual;
            decimal booked;
            if (!StockUnits.Dec(CellOf(row, real), out actual) || !StockUnits.Dec(CellOf(row, book), out booked))
            {
                return;
            }
            decimal diff = actual - booked;
            Put(names, row, gain, StockUnits.Price(diff > 0m ? diff : 0m));
            Put(names, row, loss, StockUnits.Price(diff < 0m ? -diff : 0m));
        }

        // U8 的 DMO：同一仓库只能有一张未审核的盘点单。
        internal static void RefuseOpenCheck(object conn, string wh)
        {
            if (Rows.Scalar(conn, OpenCheckSql, new object[] { wh ?? "" }) != null)
            {
                throw new BridgeException(409, "state_mismatch", "该仓库已有未审核的盘点单");
            }
        }

        // U8 的 DMO 也拒绝存货、批号、自由项都相同的行。pairs 的第 0 项是 CheckKey。
        static void CheckNoDup(List<string[]> pairs)
        {
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < pairs.Count; i++)
            {
                if (!seen.Add(pairs[i][0]))
                {
                    throw new BridgeException(400, "bad_request", "盘点单不能有存货、批号和自由项都相同的行");
                }
            }
        }
    }
}
