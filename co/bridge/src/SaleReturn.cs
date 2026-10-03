using System.Collections.Generic;

namespace U8Co
{
    // 退货单（红字发货单）sale_return：与发货单同表 DispatchList / DispatchLists，按 bReturnFlag=1 区分；
    // 销售 CO 用 VT 10（DispatchRed）、卡片 03，模板 iVTid 75。读取、删除在这里，审核走 SalesVerify，
    // 参照蓝字发货单生成走 SaleGen.Return（SaleReturnGen.cs）。现有 dispatch 类型仍只收蓝字。
    internal static class SaleReturn
    {
        public const string KindName = "sale_return";
        // 未覆盖：VoucherTypeSA 10 = DispatchRed（注册类型库里的枚举序号，未在测试账套跑过）。
        public const int RedVt = 10;
        public const string RedCard = "03";
        const string ShapeSql = "select cVouchType, bReturnFlag, bFirst, iverifystate from DispatchList where DLID=?";
        const string OutSql = "select top 1 convert(varchar(20), r.AutoID) from rdrecords32 r "
            + "where r.iDLsID<>0 and r.iDLsID in (select s.iDLsID from DispatchLists s where s.DLID=? and s.iDLsID<>0)";
        const string BillSql = "select top 1 convert(varchar(20), b.AutoID) from SaleBillVouchs b "
            + "where b.iDLsID<>0 and b.iDLsID in (select s.iDLsID from DispatchLists s where s.DLID=? and s.iDLsID<>0)";

        public static bool Is(VoucherKind kind)
        {
            return kind != null && kind.Name == KindName;
        }

        public static ApiResult Load(WorkContext ctx, VoucherKind kind, int id)
        {
            RequireRed(Shape(ctx.Conn, id));
            return SaleOrderCo.Load(ctx, kind, id);
        }

        // 未审核、不在审批中、没有销售出库和销售发票引用。删除前每行 editprop 标 D（同销售发票），VT 10 的 CO Delete，
        // 同一事务里核对 U8 退回了原发货行和订单行的回写。
        public static ApiResult Delete(WorkContext ctx, VoucherKind kind, int id)
        {
            if (!Is(kind))
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持删除");
            }
            Dictionary<string, object> snap = CoRows.HeadRow(ctx.Conn, kind, id);
            if (snap == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            Dictionary<string, object> shape = Shape(ctx.Conn, id);
            RequireRed(shape);
            snap["vstate"] = CoRows.Col(shape, "iverifystate");
            SaleOrderCo.RefuseRunning(ctx.Conn, kind, id, snap, "单据正在审批，不能删除");
            if (CoRows.Col(snap, "verifier").Length > 0 || CoRows.Col(snap, "verify_date").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            if (Rows.Scalar(ctx.Conn, OutSql, new object[] { id }) != null
                || Rows.Scalar(ctx.Conn, BillSql, new object[] { id }) != null)
            {
                throw new BridgeException(409, "state_mismatch", "单据已有下游单据");
            }
            // 回写退回的核对在 SaleReturnDel.cs。
            return SaleGen.DeleteReturn(ctx, kind, id);
        }

        static Dictionary<string, object> Shape(object conn, int id)
        {
            Dictionary<string, object> shape = Rows.One(conn, ShapeSql, new object[] { id });
            if (shape == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            return shape;
        }

        // 退货单必须 bReturnFlag=1、cVouchType=05。蓝字发货单用 type=dispatch。
        static void RequireRed(Dictionary<string, object> shape)
        {
            if (!CoRows.FlagOf(shape, "bReturnFlag") || CoRows.Col(shape, "cVouchType") != "05")
            {
                throw new BridgeException(400, "bad_request", "该单据不是退货单（红字发货单）");
            }
        }

        const string ReturnedSql = "select top 1 convert(varchar(20), x.iDLsID) from DispatchLists x "
            + "inner join DispatchList xh on xh.DLID=x.DLID where xh.bReturnFlag=1 and isnull(x.iCorID,0)<>0 "
            + "and x.iCorID in (select s.iDLsID from DispatchLists s where s.DLID=? and s.iDLsID<>0)";

        // SalesVerify 用：蓝字发货单弃审前，若已有退货单行（iCorID）指向它的行则拒绝。
        internal static void RefuseReturned(object conn, bool dispatch, string action, int id)
        {
            if (!dispatch || action != "unverify")
            {
                return;
            }
            if (Rows.Scalar(conn, ReturnedSql, new object[] { id }) != null)
            {
                throw new BridgeException(409, "state_mismatch", "发货单已有退货单，请先删除退货单");
            }
        }

        // SalesVerify 用：退货单审核前核对红字，蓝字、期初照旧只走 dispatch。
        internal static void RefuseVerify(HeadState head)
        {
            if (!head.Red || head.VouchType != "05")
            {
                throw new BridgeException(400, "bad_request", "该单据不是退货单（红字发货单）");
            }
        }
    }
}
