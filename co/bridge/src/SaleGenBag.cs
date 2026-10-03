using System.Collections.Generic;

namespace U8Co
{
    internal static partial class SaleGen
    {
        static HeadFill HeadFillOf(object sys, object srcHead, Dictionary<string, object> head, string soCode, string card)
        {
            HeadFill fill = new HeadFill();
            fill.Sys = sys;
            fill.SrcHead = srcHead;
            fill.Head = head;
            fill.SoCode = soCode;
            fill.Card = card;
            return fill;
        }

        static LineFill LineFillOf(object co, Dictionary<string, object> head, string soCode)
        {
            LineFill fill = new LineFill();
            fill.Co = co;
            fill.Head = head;
            fill.SoCode = soCode;
            return fill;
        }

        // 发货单修改新增行（SaleEditMore.AddLines）：同生单 FillLine，从 sale_RefSOVouch_B 取订单行、
        // 去掉主键和累计列、按数量缩放金额、BodyCheck iquantity，写仓库、来源订单行、订单号、行号和 editprop=A。
        internal static void EditAddLine(WorkContext ctx, object co, object[] doms, SaleEditRow row, int rowNo)
        {
            LineFill fill = LineFillOf(co, new Dictionary<string, object>(), row.SoCode ?? "");
            ShipLine line = new ShipLine();
            line.LineId = row.SoLine;
            line.Qty = row.New;
            line.OrderRow = row.OrderRow ?? "";
            line.Fields = new Dictionary<string, object>();
            foreach (KeyValuePair<string, string> pair in row.Fields)
            {
                line.Fields[pair.Key] = pair.Value;
            }
            FillLine(ctx, doms, fill, line, rowNo - 1);
        }

        static SaveEcho Echo(string idSql, string idColumn, string sourceName, int sourceId)
        {
            SaveEcho echo = new SaveEcho();
            echo.IdSql = idSql;
            echo.IdColumn = idColumn;
            echo.SourceName = sourceName;
            echo.SourceId = sourceId;
            return echo;
        }

        sealed class HeadFill
        {
            internal object Sys;
            internal object SrcHead;
            internal Dictionary<string, object> Head;
            internal string SoCode;
            internal string Card;
        }

        sealed class LineFill
        {
            internal object Co;
            internal Dictionary<string, object> Head;
            internal string SoCode;
        }

        sealed class SaveEcho
        {
            internal string IdSql;
            internal string IdColumn;
            internal string SourceName;
            internal int SourceId;
        }
    }
}
