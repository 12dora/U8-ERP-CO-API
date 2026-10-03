using System.Collections.Generic;

namespace U8Co
{
    // 采购入库生单（参照采购订单、来料检验单、采购退货单）的货位核对，规则和用语同 MfgGen.CheckPositions、StockPurInPos：
    // 货位管理仓库（Warehouse.bWhPos）每行必须填该仓库的末级货位，未启用的仓库不能填。不核对时 U8 照样保存，
    // 货位仓的行 cPosition 为空、也不写 InvPosition（实测），所以生单前一律在桥里拦住。
    internal static partial class StockGen
    {
        // 仓库不存在 400（field 为 head.cwhcode）；货位不对 400（field 为 lines.<i>.cposition）。positions 按请求行的顺序。
        internal static void CheckGenPositions(object conn, string wh, IList<string> positions)
        {
            bool pos;
            try
            {
                pos = StockPurInPos.WhPos(conn, wh);
            }
            catch (BridgeException ex)
            {
                throw ex.WithField("head.cwhcode");
            }
            for (int i = 0; i < positions.Count; i++)
            {
                Dictionary<string, object> row = new Dictionary<string, object>();
                row["cposition"] = positions[i] ?? "";
                try
                {
                    StockPurInPos.CheckLine(conn, wh, pos, row, true);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex.WithField("cposition"), "lines", i);
                }
            }
        }
    }
}
