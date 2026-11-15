using System;

namespace U8Co
{
    // --selftest 的锁定部分：只测纯逻辑（锁定 / 解锁闸门、采购订单拒绝、别人锁定时的修改闸门、权限登记），不连库、不建 COM。
    internal static class VoucherLockSelfTest
    {
        public static void Run()
        {
            CheckGate();
            CheckOthers();
            CheckRules();
        }

        static void CheckGate()
        {
            Gated("lock verified so", "已审核的销售订单不能锁定", "sale_order", true, "", true);
            Gated("lock held", "单据已被 乙 锁定", "sale_order", false, "乙", true);
            Gated("unlock free", "单据未锁定", "sale_order", false, " ", false);
            Gated("unlock other", "单据由 乙 锁定", "purchase_order", false, "乙", false);
            Expect("lock free", !VoucherLock.Gate("sale_order", false, "", "甲", true));
            Expect("unlock self", !VoucherLock.Gate("sale_order", false, "甲 ", "甲", false));
            // 本人已锁定：锁定按成功返回（幂等重试），已审核也一样。
            Expect("lock held self", VoucherLock.Gate("purchase_order", false, "甲", "甲", true)
                && VoucherLock.Gate("sale_order", true, "甲", "甲", true));
            // 采购订单已审核不在桥里拦，交给 U8。
            Expect("lock verified po", !VoucherLock.Gate("purchase_order", true, "", "甲", true));
            Expect("want lock", VoucherLock.WantLock("lock") && !VoucherLock.WantLock("unlock"));
        }

        static void CheckOthers()
        {
            Expect("blocks other", VoucherLockGate.Blocks("乙", "甲"));
            Expect("self passes", !VoucherLockGate.Blocks("甲", "甲"));
            Expect("free passes", !VoucherLockGate.Blocks("", "甲") && !VoucherLockGate.Blocks(null, "甲"));
            Expect("no user blocks", VoucherLockGate.Blocks("乙", ""));
            // 姓名超过列宽（20）时 U8 写入的是截断后的值。
            string longName = new string('名', 25);
            Expect("truncated self", !VoucherLockGate.Blocks(new string('名', 20), longName)
                && VoucherLockGate.SameUser(new string('名', 20), longName + " "));
            Expect("truncated other", VoucherLockGate.Blocks(new string('名', 19) + "甲", longName));
            Expect("sql kinds", VoucherLock.LockerSql("sale_order") != null && VoucherLock.LockerSql("purchase_order") != null
                && VoucherLock.LockerSql("dispatch") == null && VoucherLock.LockerSql("") == null);
            Expect("lockable", VoucherLock.Lockable(Kinds.Find("sale_order")) && !VoucherLock.Lockable(Kinds.Find("purchase_order"))
                && !VoucherLock.Lockable(Kinds.Find("dispatch")) && !VoucherLock.Lockable(null));
            Refused("refuse po", VoucherLock.PuUnsupported, Kinds.Find("purchase_order"));
            Refused("refuse dispatch", "该单据类型不支持锁定", Kinds.Find("dispatch"));
        }

        static void CheckRules()
        {
            Expect("rule lock", PermRegistry.ForKey(VoucherLock.SaLockRule) != null);
            Expect("rule unlock", PermRegistry.ForKey(VoucherLock.SaUnlockRule) != null);
        }

        static void Gated(string name, string message, string kind, bool verified, string locker, bool locking)
        {
            try
            {
                VoucherLock.Gate(kind, verified, locker, "甲", locking);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 409 && ex.Code == "state_mismatch" && ex.Message == message);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void Refused(string name, string message, VoucherKind kind)
        {
            try
            {
                VoucherLock.Refuse(kind);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request" && ex.Message == message);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }
    }
}
