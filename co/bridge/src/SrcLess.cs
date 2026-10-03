using System;
using System.Collections.Generic;

namespace U8Co
{
    // 无来源新增（vouchers/create）的入口：发货单（SA VT 9 卡片 01，不挂订单）、先开票销售发票（idisp=0，不挂发货单，
    // U8 自己生成发货单）、到货单（PU vt 2，不挂订单）、材料出库单（USERPCO.Insert("11")，csource=库存）。
    // 顺序：字段校验 → 账套选项（打开「必有订单」一类选项时 409，同无来源采购入库 StockPurInPos.RefuseHavePo）→ 调用 U8。
    internal static class SrcLess
    {
        const string OptSql = "select cValue from AccInformation where cSysID=? and cName=?";

        // Dispatch.CreateOf 的钩子：不是这四类（及的销售出库单）返回 null。
        public static ApiResult TryCreate(WorkContext ctx, VoucherKind kind)
        {
            // 无来源销售出库单：StockSaleOut 自己做名单、选项和档案预检，再走其他出库的 StockCo.Create。
            if (kind != null && kind.Name == "sale_out")
            {
                return StockSaleOut.Create(ctx, kind, ctx.Item.Head, ctx.Item.Lines);
            }
            if (!SrcLessReq.Handles(kind))
            {
                return null;
            }
            Dictionary<string, object> head = ctx.Item.Head;
            object[] lines = ctx.Item.Lines;
            SrcLessReq.Check(kind, head, lines);
            RefuseOption(ctx.Conn, kind);
            if (kind.Name == "arrival")
            {
                return PuArr.CreateFree(ctx, kind, head, lines);
            }
            if (kind.Name == "material_out")
            {
                return MfgGen.MaterialOutFree(ctx, kind, head, lines);
            }
            return SrcLessSa.Create(ctx, kind, head, lines);
        }

        // DocLocks.KeysOf 的新增锁：先开票的发票会让 U8 生成发货单（共用 cDLCode 编号），另锁 new:dispatch；
        // 到货单与采购退货单同表同编号，另锁 new:purchase_return（同生单的互锁）。
        public static string[] CreateLocks(string kind)
        {
            if (kind == "sale_invoice")
            {
                return new string[] { "new:sale_invoice", "new:dispatch" };
            }
            if (kind == "arrival")
            {
                return new string[] { "new:arrival", "new:purchase_return" };
            }
            return new string[] { "new:" + kind };
        }

        // 选项口径同 StockPurInPos.RefuseHavePo：409 state_mismatch，消息带 U8 选项名。
        // ST.ballowAddnewVouch 的选项名是「领料必有订单」：为 true 时 U8 拒绝无来源领料（USERPBO 资源 00690，实测）。
        internal static void RefuseOption(object conn, VoucherKind kind)
        {
            string name = kind.Name;
            if ((name == "dispatch" || name == "sale_invoice") && IsOn(conn, "SA", "bMustSO_ptxs", false))
            {
                throw Refused("账套设置了普通销售必有订单（SA.bMustSO_ptxs）", kind);
            }
            if (name == "arrival" && IsOn(conn, "PU", "bPTHavePO", false))
            {
                throw Refused("账套设置了普通业务必有订单（PU.bPTHavePO）", kind);
            }
            if (name == "material_out" && IsOn(conn, "ST", "ballowAddnewVouch", false))
            {
                throw Refused("账套设置了领料必有订单（ST.ballowAddnewVouch）", kind);
            }
        }

        static BridgeException Refused(string why, VoucherKind kind)
        {
            return new BridgeException(409, "state_mismatch", why + "，不能无来源录入" + Plain(kind));
        }

        static string Plain(VoucherKind kind)
        {
            // 发货单的 Title 带「(蓝字)」，消息里去掉。
            string title = kind.Title ?? kind.Name;
            int cut = title.IndexOf('(');
            return cut > 0 ? title.Substring(0, cut) : title;
        }

        // 账套选项：1 / true（不分大小写）为真；没有这一项时用 missing。
        internal static bool IsOn(object conn, string sys, string name, bool missing)
        {
            string value = Rows.Scalar(conn, OptSql, new object[] { sys, name });
            if (value == null)
            {
                return missing;
            }
            string text = value.Trim();
            return text == "1" || text.Equals("true", StringComparison.OrdinalIgnoreCase);
        }
    }
}
