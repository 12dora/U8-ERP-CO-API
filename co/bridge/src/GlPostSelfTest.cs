using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的记账部分：只测纯逻辑（请求校验、锁键、@tcond、连接串转换、期初对账结果判断、错误归类、权限登记），
    // 不连库、不加载 U8 组件。
    internal static class GlPostSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckKeys();
            CheckCond();
            CheckConn();
            CheckReconcile();
            CheckErr();
            Expect("rule", PermRegistry.ForKey(GlPostParse.Rule) != null);
            // 取消记账（GlUnpostSelfTest）挂在这里，SelfTest.Run 已到函数长度上限。
            GlUnpostSelfTest.Run();
            // 红字冲销（GlReverseSelfTest）同上。
            GlReverseSelfTest.Run();
            // 期间损益结转、自定义转账（GlTransferSelfTest）同上。
            GlTransferSelfTest.Run();
        }

        static Dictionary<string, object> Body(object vouchers)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["period"] = 9;
            body["vouchers"] = vouchers;
            return body;
        }

        static object[] Two()
        {
            return new object[] { Voucher("转", 3), Voucher("收", 12) };
        }

        static Dictionary<string, object> Voucher(string sign, object no)
        {
            Dictionary<string, object> v = new Dictionary<string, object>();
            v["sign"] = sign;
            v["no"] = no;
            return v;
        }

        static void CheckParse()
        {
            GlPostReq req = GlPostParse.Parse(Body(Two()), 2026);
            Expect("parse", req.Year == 2026 && req.Period == 9 && req.Items.Count == 2 && req.Items[1].No == 12);
            Dictionary<string, object> body = Body(Two());
            body["fiscal_year"] = 2025;
            Expect("fiscal", GlPostParse.Parse(body, 2026).Year == 2025);
            Bad("empty", Body(new object[0]));
            Bad("dup", Body(new object[] { Voucher("转", 3), Voucher("转", 3) }));
            Bad("no str", Body(new object[] { Voucher("转", "3") }));
            Bad("no range", Body(new object[] { Voucher("转", 40000) }));
            Dictionary<string, object> extra = Voucher("转", 3);
            extra["period"] = 9;
            Bad("extra", Body(new object[] { extra }));
            Bad("null", Body(null));
            object[] many = new object[GlPostParse.Max + 1];
            for (int i = 0; i < many.Length; i++)
            {
                many[i] = Voucher("转", i + 1);
            }
            Bad("many", Body(many));
        }

        static void CheckKeys()
        {
            string[] keys = GlPostParse.LockKeys(Body(Two()), "2026");
            Expect("keys", keys.Length == 3 && keys[0] == "gl:post" && keys[1] == "gl:2026:9:转:3" && keys[2] == "gl:2026:9:收:12");
            Dictionary<string, object> body = Body(new object[] { Voucher("转", 3), "x" });
            body["fiscal_year"] = 2025;
            keys = GlPostParse.LockKeys(body, "2026");
            Expect("keys fiscal", keys.Length == 2 && keys[1] == "gl:2025:9:转:3");
            Expect("keys none", GlPostParse.LockKeys(null, "2026").Length == 1);
        }

        static void CheckCond()
        {
            GlPostReq req = GlPostParse.Parse(Body(new object[] { Voucher("转", 7), Voucher("收", 1), Voucher("转", 2) }), 2026);
            req.Items[0].Seq = 4;
            req.Items[1].Seq = 1;
            req.Items[2].Seq = 4;
            Expect("cond", GlPostParse.Cond(req) == "(isignseq=1 and ino_id in (1)) or (isignseq=4 and ino_id in (2,7))");
            req.Master = true;
            Expect("cond master", GlPostParse.Cond(req) == "(A.isignseq=1 and A.ino_id in (1)) or (A.isignseq=4 and A.ino_id in (2,7))");
            Expect("has", req.Has(2026, 9, 4, 7) && !req.Has(2025, 9, 4, 7) && !req.Has(2026, 8, 4, 7) && !req.Has(2026, 9, 1, 7));
        }

        static void CheckConn()
        {
            string text = GlPostSql.Text("Provider=SQLOLEDB;Data Source=db.example.com,1433;Initial Catalog=UFDATA_803_2026;"
                + "User ID=u8;Password=p;Use Procedure for Prepare=1;Auto Translate=True");
            System.Data.SqlClient.SqlConnectionStringBuilder b = new System.Data.SqlClient.SqlConnectionStringBuilder(text);
            Expect("conn", b.DataSource == "db.example.com,1433" && b.InitialCatalog == "UFDATA_803_2026" && b.UserID == "u8"
                && b.Password == "p" && b.Pooling && b.Enlist && b.MinPoolSize == 1);
            Expect("conn provider", text.IndexOf("Provider", StringComparison.OrdinalIgnoreCase) < 0);
            Expect("conn stable", GlPostSql.Text(text) == text);
        }

        static void CheckReconcile()
        {
            Hashtable all = new Hashtable();
            all["GLUpper_Lower"] = true;
            all["GLSum_Ass"] = true;
            all["GLSum_MultiAss"] = true;
            all["GLAss_Vouch"] = true;
            all["GLAss_MultiAss"] = false;
            Expect("qc ok", GlPostFirst.Unbalanced(all).Count == 0);
            all["GLSum_Ass"] = false;
            all.Remove("GLAss_Vouch");
            List<string> bad = GlPostFirst.Unbalanced(all);
            Expect("qc bad", bad.Count == 2 && bad[0] == "GLSum_Ass" && bad[1] == "GLAss_Vouch");
            Expect("qc null", GlPostFirst.Unbalanced(null).Count == 4);
        }

        static void CheckErr()
        {
            Status("commit aborted", GlPostErr.Commit(new System.Transactions.TransactionAbortedException("x")), 503);
            Status("commit doubt", GlPostErr.Commit(new System.Transactions.TransactionInDoubtException("x")), 504);
            Status("commit other", GlPostErr.Commit(new InvalidOperationException("x")), 504);
            Status("u8 other", GlPostErr.U8(new InvalidOperationException("x")), 409);
            Status("u8 timeout", GlPostErr.U8(new InvalidOperationException("x", new TimeoutException("t"))), 503);
            Status("own other", GlPostErr.Own(new InvalidOperationException("x")), 500);
            Status("before aborted", GlPostErr.Before(new System.Transactions.TransactionAbortedException("x")), 503);
            Status("before dtc", GlPostErr.Before(new System.Transactions.TransactionManagerCommunicationException("x")), 503);
            Status("before bridge", GlPostErr.Before(GlState.Refuse("x")), 409);
            Status("before other", GlPostErr.Before(new FormatException("x")), 500);
        }

        static void Status(string name, Exception ex, int status)
        {
            BridgeException bridge = ex as BridgeException;
            Expect("err " + name, bridge != null && bridge.Status == status);
        }

        static void Bad(string name, Dictionary<string, object> body)
        {
            try
            {
                GlPostParse.Parse(body, 2026);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request");
                return;
            }
            throw new InvalidOperationException("glpost " + name);
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("glpost " + name);
            }
        }
    }
}
