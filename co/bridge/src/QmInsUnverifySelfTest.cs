using System;

namespace U8Co
{
    // --selftest 的产品报检单单独弃审部分：类型登记、预演模式、写规则与功能 id、登录前的 action 校验；
    // 产品报检单不开放修改。不连库、不建 COM。
    internal static class QmInsUnverifySelfTest
    {
        const string Kind = "qm_product_inspect";

        public static void Run()
        {
            CheckWiring();
            CheckPreLogin();
        }

        static void CheckWiring()
        {
            VoucherKind k = Kinds.Find(Kind);
            True("ins unverify kind", k != null && k.Verifiable && k.VerifySub == QmCo.LoginSub && QmInsUnverify.Handles(k));
            True("ins no update", !k.Updatable && !QmEditReq.Handles(k) && PermRegistry.ForKey("write:" + Kind + ":update") == null);
            Expect("ins verify dry", "validate", DryRunModes.Lookup("vouchers/verify", Kind, "unverify", ""));
            True("ins rules", PermRegistry.ForKey("write:" + Kind + ":unverify") != null
                && PermRegistry.ForKey("write:" + Kind + ":verify") == null);
            Expect("ins unverify auth", "QM02020107", QmSpec.Find(Kind).UnverifyAuth);
            VoucherKind incoming = Kinds.Find("qm_incoming_inspect");
            True("ins incoming fixed", !incoming.Updatable && !incoming.Verifiable && !QmInsUnverify.Handles(incoming));
        }

        // 产品报检单 vouchers/verify 只收 unverify（登录前）；别的路由不管。
        static void CheckPreLogin()
        {
            WorkItem item = new WorkItem();
            item.Type = Kinds.Find(Kind);
            item.Action = "unverify";
            QmInsUnverify.PreLogin(item, "/u8co/v1/vouchers/verify");
            item.Action = "verify";
            QmInsUnverify.PreLogin(item, "/u8co/v1/vouchers/delete");
            try
            {
                QmInsUnverify.PreLogin(item, "/u8co/v1/vouchers/verify");
            }
            catch (BridgeException ex)
            {
                True("ins prelogin verify", ex.Status == 400 && ex.Field == "action");
                return;
            }
            throw new InvalidOperationException("ins prelogin verify: no error");
        }

        static void True(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }

        static void Expect(string name, string want, string got)
        {
            if (!string.Equals(want, got, StringComparison.Ordinal))
            {
                throw new InvalidOperationException(name + ": " + got);
            }
        }
    }
}
