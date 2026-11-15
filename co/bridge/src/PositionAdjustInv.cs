using System.Collections.Generic;

namespace U8Co
{
    // 货位调整单新增前的存货管理方式核对（任何 COM 调用之前，400 带行字段）。桥的结存键只有货位、存货、批号、
    // 自由项 1–10，不写保质期日期、VMI 供应商、跟踪号这些维度，所以：保质期管理（Inventory.bInvQuality=1）的存货拒绝；
    // 调出货位上该存货有 VMI 供应商（InvPositionSum.cvmivencode 非空）的结存拒绝；批次管理（bInvBatch=1）必须给批号，
    // 非批次管理不能给批号（否则按空批号查结存，报的是误导人的结存不足）。
    internal static class PositionAdjustInv
    {
        const string InvSql = "select convert(varchar(5), isnull(bInvBatch, 0)) as batch,"
            + " convert(varchar(5), isnull(bInvQuality, 0)) as shelf from Inventory where cInvCode=?";
        const string VmiSql = "select top 1 cvmivencode from InvPositionSum where cWhCode=? and cPosCode=? and cInvCode=?"
            + " and isnull(cvmivencode, N'')<>N''";

        internal static void Require(object conn, string wh, BinLine line, string at)
        {
            Dictionary<string, object> inv = Rows.One(conn, InvSql, new object[] { line.Inv });
            if (inv == null)
            {
                throw BridgeException.BadField(FieldPath.Join(at, "cinvcode"), "存货 " + line.Inv + " 不存在");
            }
            if (StockMsg.Col(inv, "shelf") == "1")
            {
                throw BridgeException.BadField(FieldPath.Join(at, "cinvcode"), "存货 " + line.Inv + " 是保质期管理，暂不支持货位调整");
            }
            bool batched = StockMsg.Col(inv, "batch") == "1";
            if (batched && line.Batch.Length == 0)
            {
                throw BridgeException.BadField(FieldPath.Join(at, "cbatch"), "存货 " + line.Inv + " 是批次管理，必须填批号 cbatch");
            }
            if (!batched && line.Batch.Length > 0)
            {
                throw BridgeException.BadField(FieldPath.Join(at, "cbatch"), "存货 " + line.Inv + " 不是批次管理，不能填批号");
            }
            if (Rows.Scalar(conn, VmiSql, new object[] { wh, line.From, line.Inv }) != null)
            {
                throw BridgeException.BadField(FieldPath.Join(at, "cbposcode"), "货位 " + line.From + " 上存货 " + line.Inv
                    + " 有代管（VMI）结存，暂不支持货位调整");
            }
        }
    }
}
