using System;
using System.Text;

namespace U8Co
{
    // 契约公布的测试向量，不是生产密钥。
    internal static class SelfTest
    {
        // 失败时指出是哪个自检方法（调用栈里最近的 *SelfTest 帧），便于在没有 U8 的机器上定位。
        static int Fail(Exception ex)
        {
            Console.Error.WriteLine("selftest 失败: " + ex.Message);
            Console.Error.WriteLine(FailedAt(ex));
            return 1;
        }

        static string FailedAt(Exception ex)
        {
            string trace = ex.StackTrace ?? "";
            foreach (string line in trace.Split('\n'))
            {
                if (line.IndexOf("SelfTest.", StringComparison.Ordinal) >= 0)
                {
                    return "位置: " + line.Trim();
                }
            }
            return "位置: 未知";
        }

        internal const string SecretHex = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff";
        const string MacHex = "a85c2c38ccf25d1c837ec12b0a3707ee6aa584ed7834f8eac3302f81098b6407";
        const string EncHex = "3d9103ebd3f1448ce0dab668a4ce27c51a2ff0ac0b10c90cc5c300520e3411bc";
        internal const string PasswordEnc = "Dw4NDAsKCQgHBgUEAwIBAK86wvyLgq4AmzNMoENmeZU=";
        const string Password = "测试Pass#01";
        const string BodySha = "98c5d2571d4765701ddcfc1ade19102114b4c8658a0d4b4e3596ece98084c106";
        const string Sig = "dfc80957a80e21188add47a0bea3cfc917379139105a48337f91933a43b27a3a";
        const string Ts = "1790434800";
        const string Nonce = "a1b2c3d4e5f60718293a4b5c6d7e8f90";
        const string Path = "/u8co/v1/sale-orders/verify";
        const long Now = 1790434800L;
        // 文档用地址（RFC 5737），只在自检里用。
        const string Client = "203.0.113.5";

        public static int Run()
        {
            try
            {
                CheckKeys();
                CheckPassword();
                CheckSignature();
                CheckAuthorize();
                CheckRules();
                IdemSelfTest.Run();
                PermSelfTest.Run();
                LicenseSelfTest.Run();
                MoCreateSelfTest.Run();
                MoUpdateSelfTest.Run();
                BomSelfTest.Run();
                ArcGlSelfTest.Run();
                VoucherLockSelfTest.Run();
                StockMiscSelfTest.Run();
                QmSelfTest.Run();
                // 不良品处理单（QmRejSelfTest）。
                QmRejSelfTest.Run();
                ArcPartnerSelfTest.Run();
                // 原因码档案（ArcReasonSelfTest）。
                ArcReasonSelfTest.Run();
                // 档案列表 keys_only、end_date / disabled（ArcListExtraSelfTest）。
                ArcListExtraSelfTest.Run();
                // 票据列表 ar_note / ap_note、单张读取 notes/get（NotesReadSelfTest）。
                NotesReadSelfTest.Run();
                // 单据与经营管理报表（RunVouchersAndReports）。
                RunVouchersAndReports();
                SrcLessSelfTest.Run();
                // 写闸门与写入策略（RunWritePolicy）。
                RunWritePolicy();
                // 名称解析、幂等结果查询（ArcResolveSelfTest）。
                ArcResolveSelfTest.Run();
                // 单据搜索、批量读取（VoucherSearchSelfTest、LoadManySelfTest）。
                VoucherSearchSelfTest.Run();
                // 账龄分析 {INNER} 的 ? 顺序、default_credit_days（ReportsAgingSelfTest）。
                ReportsAgingSelfTest.Run();
                LoadManySelfTest.Run();
                // 字段说明（MetaFieldsSelfTest）。
                MetaFieldsSelfTest.Run();
                GlPostSelfTest.Run();
                // 期初记账 openings/post（OpeningPostSelfTest）。
                OpeningPostSelfTest.Run();
                // 外币核销（同币种同汇率）、自动核销按币种配对、预收行（ArapWriteoffSelfTest）。
                ArapWriteoffSelfTest.Run();
                // 应收应付其他处理与票据处理（RunArapProc）。
                RunArapProc();
                // 汇兑损益 arap/exchange_gain、取消（ArapExGainSelfTest）。
                ArapExGainSelfTest.Run();
                // 计提坏账准备 arap/bad_debt provision（ArapBadProvisionSelfTest）。
                ArapBadProvisionSelfTest.Run();
                // 坏账处理 arap/bad_debt 的请求、坏账发生 / 收回（ArapBadSelfTest）。
                ArapBadSelfTest.Run();
                // 凭证摘要 gl/vouchers/digest（GlDigestSelfTest）。
                GlDigestSelfTest.Run();
                // 应收 / 应付期初单据 openings/arap（OpeningsArapSelfTest）。
                OpeningsArapSelfTest.Run();
                // 应收 / 应付合并制单 arap/voucher ids（ArapVoucherSelfTest）。
                ArapVoucherSelfTest.Run();
                // 处理制单 arap/process/voucher（ArapProcVoucherSelfTest）。
                ArapProcVoucherSelfTest.Run();
                // 外币应收 / 应付单的行币种按科目定（ArapLineFxSelfTest）。
                ArapLineFxSelfTest.Run();
                // 库存期初结存 stock_opening（StockOpeningSelfTest：EAI storeqc 报文、返回解析、新单对应、闸门）。
                StockOpeningSelfTest.Run();
                // 月末结账 periods/close（PeriodCloseSelfTest）。
                PeriodCloseSelfTest.Run();
                // 存货核算记账、期末处理 ia/post、ia/period_end（IaSelfTest）。
                IaSelfTest.Run();
                // 账套体检 reports/account_readiness（ReportsReadinessSelfTest）。
                ReportsReadinessSelfTest.Run();
                // 登录复用（LoginCacheSelfTest，不建 COM）。
                LoginCacheSelfTest.Run();
                SqlScriptSelfTest.Run();
                // 写预演（DryRunModes、DryRunReq、DryRunValue、meta）。
                DryRunSelfTest.Run();
                Console.WriteLine("selftest 通过");
                return 0;
            }
            catch (Exception ex)
            {
                return Fail(ex);
            }
        }

        // 写闸门与写入策略的自检（从 Run 拆出，控制函数长度）。
        static void RunWritePolicy()
        {
            WriteGateSelfTest.Run();
            // 写入分类与两处策略检查、测试账套调用点登记表（WriteClassSelfTest）。
            WriteClassSelfTest.Run();
            // 只读账套（ReadOnlyGateSelfTest）。
            ReadOnlyGateSelfTest.Run();
            // 第二级写入路由矩阵：每条路由经 Requests.ForPath 的开关、测试账套、只读账套顺序（TestAccountRouteSelfTest）。
            TestAccountRouteSelfTest.Run();
            // 写入策略（WritePolicySelfTest）。
            WritePolicySelfTest.Run();
            // 写入限额与许可保护（WriteQuotaSelfTest）。
            WriteQuotaSelfTest.Run();
        }

        // 单据（采购结算、调整单、退货申请、红冲、到货单关闭、固定资产）与经营管理报表的自检（从 Run 拆出，控制函数长度）。
        static void RunVouchersAndReports()
        {
            // 采购结算单只读（PuSettleSelfTest）。
            PuSettleSelfTest.Run();
            // 出入库调整单、存货调价单只读，固定资产变动单与折旧报表（IaSaFaSelfTest）。
            IaSaFaSelfTest.Run();
            // 退货申请单只读（ReturnsApplySelfTest）。
            ReturnsApplySelfTest.Run();
            // 红冲蓝字销售发票（SaleBlueRedSelfTest）。
            SaleBlueRedSelfTest.Run();
            // 经营管理报表（总账口径）mgmt/pnl、mgmt/meta、mgmt/cash_stock（MgmtGlSelfTest）。
            MgmtGlSelfTest.Run();
            // 经营管理销售分析、往来账期 mgmt/sales、mgmt/arap_terms（ReportsMgmtSalesSelfTest、ReportsMgmtArapSelfTest）。
            ReportsMgmtSalesSelfTest.Run();
            ReportsMgmtArapSelfTest.Run();
            // 到货单关闭 / 打开（PuArrCloseSelfTest）。
            PuArrCloseSelfTest.Run();
            // 固定资产卡片新增 / 撤销、变动单新增、设备台账（FaWriteSelfTest）。
            FaWriteSelfTest.Run();
        }

        // 应收应付其他处理、票据处理的自检（从 Run 拆出，控制函数长度）。
        static void RunArapProc()
        {
            // 取消应收冲应付 / 应付冲应收 / 并账 arap/process/cancel（ArapProcCancelSelfTest）。
            ArapProcCancelSelfTest.Run();
            // 并账 arap/merge（ArapMergeSelfTest）。
            ArapMergeSelfTest.Run();
            // 应收冲应付 / 应付冲应收 arap/transfer（ArapTransferSelfTest）。
            ArapTransferSelfTest.Run();
            // 红票对冲 arap/red_offset（ArapRedSelfTest）。
            ArapRedSelfTest.Run();
            // 票据处理 notes/process（NotesProcSelfTest）。
            NotesProcSelfTest.Run();
            // 票据登记、删除 notes/create、notes/delete（NotesRegSelfTest）。
            NotesRegSelfTest.Run();
            // 应付票据 notes/create、notes/delete、notes/process 的 flag AP（NotesApSelfTest）。
            NotesApSelfTest.Run();
            // 制单的 cash_items、坏账收回用过的收款单（ArapCashItemsSelfTest）。
            ArapCashItemsSelfTest.Run();
            // 供应商退款 ap_refund（AP48）、客户退款 ar_refund（AR49）（ArapRefundSelfTest）。
            ArapRefundSelfTest.Run();
        }

        static void CheckKeys()
        {
            byte[] secret = Crypto.ParseHexLower(SecretHex);
            Expect("k_mac", MacHex, Crypto.Hex(Crypto.MacKey(secret)));
            Expect("k_enc", EncHex, Crypto.Hex(Crypto.EncKey(secret)));
        }

        static void CheckPassword()
        {
            byte[] secret = Crypto.ParseHexLower(SecretHex);
            string plain = Crypto.Decrypt(Crypto.EncKey(secret), PasswordEnc);
            Expect("password", Password, plain);
        }

        static void CheckSignature()
        {
            byte[] body = BodyBytes();
            Expect("body sha256", BodySha, Crypto.Hex(Crypto.Sha256(body)));
            byte[] secret = Crypto.ParseHexLower(SecretHex);
            string sig = Auth.Sign(Crypto.MacKey(secret), "POST", Path, Ts, Nonce, body);
            Expect("sig", Sig, sig);
        }

        static void CheckAuthorize()
        {
            BridgeConfig cfg = VectorConfig();
            byte[] body = BodyBytes();
            if (!Auth.Allow(cfg, Attempt(body, Nonce, Sig, Client, Now)))
            {
                throw new InvalidOperationException("authorize");
            }
            if (Auth.Allow(cfg, Attempt(body, Nonce, Sig, Client, Now)))
            {
                throw new InvalidOperationException("replay");
            }
            Reject(cfg, Attempt(body, "b1b2c3d4e5f60718293a4b5c6d7e8f90", Sig, Client, Now), "bad sig");
            string ipNonce = "c1b2c3d4e5f60718293a4b5c6d7e8f90";
            string ipSig = Auth.Sign(cfg.MacKey, "POST", Path, Ts, ipNonce, body);
            Reject(cfg, Attempt(body, ipNonce, ipSig, "198.51.100.99", Now), "bad ip");
            string lateNonce = "d1b2c3d4e5f60718293a4b5c6d7e8f90";
            string lateSig = Auth.Sign(cfg.MacKey, "POST", Path, Ts, lateNonce, body);
            Reject(cfg, Attempt(body, lateNonce, lateSig, Client, Now + 1000L), "bad time");
        }

        // 配置校验：空白名单不放行，写法不对的值启动前就拒绝。
        static void CheckRules()
        {
            BridgeConfig empty = new BridgeConfig();
            if (BridgeConfig.AllowsAccount(empty, "998") || Auth.IpAllowed(empty, Client))
            {
                throw new InvalidOperationException("empty allowlist");
            }
            ConfigRules.CheckPrefix("http://+:18089/u8co/");
            ConfigRules.CheckClients(new string[] { Client, "2001:db8::5" });
            CheckBadPrefix();
            CheckBadValues();
        }

        static void CheckBadPrefix()
        {
            ExpectThrow("prefix path", delegate { ConfigRules.CheckPrefix("http://+:18089/other/"); });
            ExpectThrow("prefix port", delegate { ConfigRules.CheckPrefix("http://+/u8co/"); });
            ExpectThrow("server", delegate { ConfigRules.CheckServer("a b"); });
            ExpectThrow("account", delegate { ConfigRules.CheckAccounts(new string[] { "99" }); });
        }

        static void CheckBadValues()
        {
            ExpectThrow("client cidr", delegate { ConfigRules.CheckClients(new string[] { "203.0.113.0/24" }); });
            ExpectThrow("client host", delegate { ConfigRules.CheckClients(new string[] { "bridge.example.com" }); });
            ExpectThrow("dir relative", delegate { Paths.CheckDir("U8SOFT", "u8Home"); });
            ExpectThrow("dir drive root", delegate { Paths.CheckDir("E:\\", "u8Home"); });
        }

        static void ExpectThrow(string name, Action act)
        {
            try
            {
                act();
            }
            catch (InvalidOperationException)
            {
                return;
            }
            throw new InvalidOperationException(name);
        }

        static AuthAttempt Attempt(byte[] body, string nonce, string sig, string ip, long now)
        {
            AuthAttempt attempt = new AuthAttempt();
            attempt.Method = "POST";
            attempt.Path = Path;
            attempt.Ts = Ts;
            attempt.Nonce = nonce;
            attempt.Sig = sig;
            attempt.Body = body;
            attempt.Ip = ip;
            attempt.Now = now;
            return attempt;
        }

        static void Reject(BridgeConfig cfg, AuthAttempt attempt, string name)
        {
            if (Auth.Allow(cfg, attempt))
            {
                throw new InvalidOperationException(name);
            }
        }

        static BridgeConfig VectorConfig()
        {
            BridgeConfig cfg = new BridgeConfig();
            cfg.Secret = Crypto.ParseHexLower(SecretHex);
            cfg.MacKey = Crypto.MacKey(cfg.Secret);
            cfg.EncKey = Crypto.EncKey(cfg.Secret);
            cfg.AllowedClients = new string[] { Client };
            cfg.AllowedAccounts = new string[] { "998" };
            return cfg;
        }

        static byte[] BodyBytes()
        {
            return Encoding.UTF8.GetBytes(
                "{\"acc\":\"998\",\"year\":\"2026\",\"operator\":\"op001\",\"password_enc\":\""
                + PasswordEnc
                + "\",\"date\":\"2026-09-26\",\"id\":9000000003,\"action\":\"verify\"}");
        }

        static void Expect(string name, string want, string got)
        {
            if (!string.Equals(want, got, StringComparison.Ordinal))
            {
                throw new InvalidOperationException(name);
            }
        }
    }
}
