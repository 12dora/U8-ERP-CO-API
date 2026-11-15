using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 删除桥生成的材料出库 / 产成品入库：未审核、来源是生产订单 / 产品检验单或产品不良品处理单、订单未关闭，
    // 然后走库存通用的 9 参数 Delete（U8 回退 IssQty、QualifiedInQty、FsumQuantity；合并检验另回退来源 FSUMQUANTITY），
    // 再在新连接上确认单据已不在。
    // 有货位记录的单据同库存删除：事务里先清货位再删。
    internal static partial class MfgGen
    {
        const string OutMoSql = "select top 1 convert(varchar(10), d.Status) from rdrecords11 b"
            + " join mom_moallocate a on a.AllocateId=b.iMPoIds join mom_orderdetail d on d.MoDId=a.MoDId"
            + " where b.ID=? and d.Status<>3 order by d.Status desc";
        const string InMoSql = "select top 1 convert(varchar(10), d.Status) from rdrecords10 b"
            + " join mom_orderdetail d on d.MoDId=b.iMPoIds where b.ID=? and d.Status<>3 order by d.Status desc";

        public static ApiResult Delete(WorkContext ctx, VoucherKind kind, int id)
        {
            string source = SourceName(kind);
            Dictionary<string, object> before = DelHead(ctx.Conn, kind, id);
            if (before == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.Col(before, "verifier").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已审核");
            }
            // 来源核对与事务里的回退核对（产品不良品处理单、生产订单 / 库存来源）在 MfgGenMo.DelSource / GuardDel。
            string guard = DelSource(kind, CoRows.Col(before, "source"), source);
            string sql = kind.StType == "11" ? OutMoSql : InMoSql;
            string status = Rows.Scalar(ctx.Conn, sql, new object[] { id });
            if (status != null)
            {
                RequireDeletable(kind, status.Trim());
            }
            string ufts = StockCall.Ufts(ctx.Conn, kind, id);
            object co = null;
            object msg = null;
            try
            {
                co = StockCall.OpenCo(ctx);
                msg = Rows.NewDom();
                int[] refs;
                object[] args = StockCall.VerifyArgs(kind, id, ctx.Conn, ufts, msg, out refs);
                StockAt at = StockCall.AtFor("Delete", args, refs, ufts);
                GuardDel(at, id, guard);
                if (guard.Length == 0 && kind.Name == "product_in")
                {
                    // 参照合并检验的产品检验单生成的：核对来源、检验单、订单行的累计入库都减回去（MfgGenMerge）。
                    GuardMergeUndo(at, id);
                }
                // 有货位记录时事务里先 ClearPosition 再以 bList=true 删除（同 StockCo.Delete，u8-notes §8）。
                StockPosGuard.ClearOnDelete(ctx, co, at, kind, id);
                StockCall.RunAt(ctx, co, at);
                if (StillThere(ctx, kind, id))
                {
                    throw new BridgeException(409, "state_mismatch", "U8 返回成功但回读状态不符");
                }
                return StockMsg.Gone(kind, id);
            }
            finally
            {
                ComUtil.Final(msg);
                ComUtil.Final(co);
            }
        }

        // 实测：产成品入库使合格入库量达到订单数量时 U8 自动关闭订单行（Status=4），删除前要先在 U8 打开。
        static void RequireDeletable(VoucherKind kind, string status)
        {
            if (status == "4" && kind.Name == "product_in")
            {
                throw new BridgeException(409, "state_mismatch",
                    "生产订单行已关闭（入库完成时 U8 自动关闭），请先在 U8 打开生产订单行再删除");
            }
            RequireReleased(status);
        }

        static string SourceName(VoucherKind kind)
        {
            string name = kind == null ? "" : kind.Name;
            if (name == "material_out")
            {
                return "生产订单";
            }
            if (name == "product_in")
            {
                return "产品检验单";
            }
            throw new BridgeException(400, "bad_request", "该单据类型不支持删除");
        }

        static Dictionary<string, object> DelHead(object conn, VoucherKind kind, int id)
        {
            string sql = "select " + kind.VerifierColumn + " as verifier, cSource as source from " + kind.HeadTable
                + " where " + kind.IdColumn + "=?";
            return Rows.One(conn, sql, new object[] { id });
        }

        // 删除已提交；回读出错时结果未知（504），单据仍在则是 U8 说成功但没删掉。
        static bool StillThere(WorkContext ctx, VoucherKind kind, int id)
        {
            try
            {
                return FreshId(ctx, kind, kind.IdColumn, id) > 0;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "MfgDelete " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "已提交删除但未能回读确认，标识 " + id.ToString(CultureInfo.InvariantCulture));
            }
        }
    }
}
