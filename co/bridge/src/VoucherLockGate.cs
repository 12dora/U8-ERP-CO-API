using System;
using System.Collections.Generic;

namespace U8Co
{
    // 销售订单 / 采购订单被别的操作员锁定（表头 cLocker 非空且不是本人姓名）时，修改、删除、审核、弃审一律 409，
    // 与 U8 界面一致。锁定人本人照常放行。其他类型不查。调用点：SaleEdit.Update、SaleOrderCo.Delete、
    // SalesVerify.Verify、PurchaseEdit.GateMutable（修改、删除）、PurchaseCo.Verify。
    internal static class VoucherLockGate
    {
        public static void Refuse(object conn, string kindName, int id, string user)
        {
            string sql = VoucherLock.LockerSql(kindName);
            if (sql == null)
            {
                return;
            }
            Dictionary<string, object> row = Rows.One(conn, sql, new object[] { id });
            string locker = row == null ? "" : CoRows.Col(row, "locker");
            if (Blocks(locker, user))
            {
                throw new BridgeException(409, "state_mismatch", "单据已被 " + locker + " 锁定");
            }
        }

        internal static bool Blocks(string locker, string user)
        {
            string held = locker == null ? "" : locker.Trim();
            return held.Length > 0 && !SameUser(held, user);
        }

        // cLocker 存操作员姓名（实测），列宽 nvarchar(20)。按 U8 写入的样子比较：两边去空格，姓名截到 20 个字符。
        internal const int LockerWidth = 20;

        internal static bool SameUser(string locker, string user)
        {
            string a = Stored(locker);
            string b = Stored(user);
            return a.Length > 0 && string.Equals(a, b, StringComparison.Ordinal);
        }

        internal static string Stored(string name)
        {
            string text = name == null ? "" : name.Trim();
            if (text.Length > LockerWidth)
            {
                text = text.Substring(0, LockerWidth).TrimEnd();
            }
            return text;
        }
    }
}
