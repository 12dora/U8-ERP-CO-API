using System;
using System.Text;

namespace U8Co
{
    // --selftest 的第二级写入路由矩阵：每条第二级写入路由都经 Requests.ForPath 的真实解析（字段合法、口令可解密），
    // 证明路由在登录前确实调用了 TestAccountGate，而不只是调用点原文正确。每行依次核对：
    // 开关关闭 → 403 feature_disabled（消息「第二级写入未开启：」加原文，账套在测试账套名单里也一样）；
    // 开关打开、账套不在名单里 → 403 test_account_only（消息为原文）；开关打开、账套在名单里 → 不再是这几种 403；
    // 只读账套（也在测试账套名单里）、开关关闭 → 403 account_read_only（只读检查先于第二级写入检查）。
    // WriteClassSelfTest.TestOnlySites 的每一行（取消制单 arap/voucher/delete 除外）都必须在下表里，新增调用点漏登会失败。
    // 取消制单只在登录后、读到处理凭证时才知道是不是第二级写入，登录前无从检查，由 ArapProcVoucherDrop 在事务里拦截。
    // 不连库、不建 COM、不登录。由 SelfTest.RunWritePolicy 调用。
    internal static class TestAccountRouteSelfTest
    {
        const string Listed = "998";
        const string Other = "997";
        const string ReadOnly = "996";

        // 路由、拒绝原文、该路由一份最小的合法请求字段（JSON 片段，不含公共字段）。
        static readonly string[][] Routes = new string[][]
        {
            new string[] { OpeningPostReq.Path, TestAccountGate.OpeningPostText, "\"module\":\"pu\",\"action\":\"post\"" },
            new string[] { OpeningsArapReq.Path, TestAccountGate.OpeningsArapText, "\"side\":\"ar\",\"action\":\"delete\",\"id\":1" },
            new string[]
            {
                PeriodCloseReq.Path, PeriodCloseReq.TestOnly, "\"module\":\"gl\",\"fiscal_year\":2026,\"period\":1,\"action\":\"close\""
            },
            new string[] { IaReq.PostPath, IaReq.TestOnly, "\"fiscal_year\":2026,\"period\":1,\"action\":\"post\"" },
            new string[] { IaReq.PeriodEndPath, IaReq.TestOnly, "\"fiscal_year\":2026,\"period\":1,\"action\":\"run\"" },
            new string[] { "/u8co/v1/vouchers/create", StockOpening.TestOnlyText, "\"type\":\"stock_opening\"" },
            new string[] { "/u8co/v1/vouchers/delete", StockOpening.TestOnlyText, "\"type\":\"stock_opening\",\"id\":1" },
            new string[] { "/u8co/v1/vouchers/verify", StockOpening.TestOnlyText, "\"type\":\"stock_opening\",\"id\":1,\"action\":\"verify\"" },
            new string[] { ArapBadReq.Path, ArapBadReq.TestOnly, "\"action\":\"provision\"" },
            new string[] { ArapExGainReq.Path, ArapExGainReq.TestOnly, "\"flag\":\"AR\",\"currency\":\"美元\"" },
            new string[] { ArapExGainReq.CancelPath, ArapExGainReq.TestOnly, "\"flag\":\"AR\"" },
            new string[]
            {
                ArapProcVoucherReq.Path, ArapProcVoucherReq.TestOnly,
                "\"flag\":\"AR\",\"cancel_nos\":[\"SYRAR0000000001\"],\"pl_code\":\"66029901\""
            },
            new string[] { ArapProcVoucherReq.Path, ArapProcVoucherReq.BadTestOnly, "\"flag\":\"AR\",\"cancel_nos\":[\"HZAR0000000001\"]" },
            new string[]
            {
                ArapProcVoucherReq.Path, ArapProcVoucherReq.ApNotesTestOnly, "\"flag\":\"AP\",\"cancel_nos\":[\"PJJAP0000000001\"]"
            },
            new string[] { ArapProcCancelReq.Path, ArapProcCancelBad.TestOnly, "\"flag\":\"AR\",\"cancel_no\":\"HZAR0000000001\"" },
            // 取消应付票据处理（CLAP）走 NotesProcReq.TestGate，原文同票据处理。
            new string[] { ArapProcCancelReq.Path, NotesProcReq.ApTestOnly, "\"flag\":\"AP\",\"cancel_no\":\"CLAP0000000001\"" },
            new string[]
            {
                NotesRegReq.CreatePath, NotesRegReq.ApTestOnly,
                "\"flag\":\"AP\",\"note_no\":\"N0001\",\"settle_code\":\"5\",\"amount\":100,\"sign_date\":\"2026-01-10\","
                + "\"receipt_date\":\"2026-01-10\",\"expire_date\":\"2026-06-10\",\"vendor\":\"S900001\",\"dept\":\"D901\","
                + "\"receiver\":\"乙公司\""
            },
            new string[] { NotesRegReq.DeletePath, NotesRegReq.ApTestOnly, "\"flag\":\"AP\",\"note_no\":\"N0001\"" },
            new string[] { NotesProcReq.Path, NotesProcReq.ApTestOnly, "\"flag\":\"AP\",\"op\":\"return\",\"note\":\"N0001\"" },
            new string[] { GlUnpostReq.Path, GlUnpostReq.TestOnly, "" },
            new string[] { GlTransferReq.PnlPath, GlTransferReq.PnlTestOnly, "\"fiscal_year\":2026,\"period\":1" },
            new string[] { GlTransferReq.CustomPath, GlTransferReq.CustomTestOnly, "\"fiscal_year\":2026,\"period\":1,\"tran_id\":\"T001\"" }
        };

        public static void Run()
        {
            CheckRegistry();
            WritePolicy.ResetForTest();
            try
            {
                for (int i = 0; i < Routes.Length; i++)
                {
                    CheckRoute(Routes[i]);
                }
            }
            finally
            {
                WritePolicy.ResetForTest();
            }
        }

        // 调用点登记表的每一行（取消制单除外）都在本表里。
        static void CheckRegistry()
        {
            string[][] sites = WriteClassSelfTest.TestOnlySites;
            for (int i = 0; i < sites.Length; i++)
            {
                if (sites[i][0] == Requests.ArapVoucherDropPath)
                {
                    continue;
                }
                Expect("route matrix lists " + sites[i][0] + " " + sites[i][2], Find(sites[i][0], sites[i][2]));
            }
        }

        static bool Find(string path, string text)
        {
            for (int i = 0; i < Routes.Length; i++)
            {
                if (Routes[i][0] == path && Routes[i][1] == text)
                {
                    return true;
                }
            }
            return false;
        }

        static void CheckRoute(string[] row)
        {
            string name = "tier2 route " + row[0] + " " + row[1];
            BridgeException off = Refusal(row, Listed, false);
            Expect(name + " flag off", off != null && off.Status == 403 && off.Code == TestAccountGate.FeatureCode
                && off.Message == TestAccountGate.FeaturePrefix + row[1] && off.Hint == TestAccountGate.FeatureHint);
            BridgeException other = Refusal(row, Other, true);
            Expect(name + " not listed", other != null && other.Status == 403 && other.Code == TestAccountGate.Code
                && other.Message == row[1] && other.Hint == TestAccountGate.Hint);
            Expect(name + " listed passes", !Gated(Refusal(row, Listed, true)));
            BridgeException ro = Refusal(row, ReadOnly, false);
            Expect(name + " read-only first", ro != null && ro.Code == ReadOnlyGate.Code);
        }

        // 第二级写入与只读账套的拒绝码；其余（含路由在闸门之后的字段校验）不算拦截。
        static bool Gated(BridgeException ex)
        {
            return ex != null && (ex.Code == TestAccountGate.FeatureCode || ex.Code == TestAccountGate.Code || ex.Code == ReadOnlyGate.Code);
        }

        // 经 Requests.ForPath 解析；放行时返回 null。
        static BridgeException Refusal(string[] row, string acc, bool enabled)
        {
            AuditDraft draft = new AuditDraft();
            draft.Path = row[0];
            try
            {
                WorkItem item = Requests.ForPath(Cfg(enabled), row[0], Body(acc, row[2]), draft);
                Expect("tier2 route known " + row[0], item != null);
                return null;
            }
            catch (BridgeException ex)
            {
                return ex;
            }
        }

        static byte[] Body(string acc, string extra)
        {
            StringBuilder json = new StringBuilder();
            json.Append("{\"acc\":\"").Append(acc).Append("\",\"year\":\"2026\",\"operator\":\"op001\",\"date\":\"2026-01-15\"");
            json.Append(",\"password_enc\":\"").Append(SelfTest.PasswordEnc).Append("\"");
            if (extra.Length > 0)
            {
                json.Append(",").Append(extra);
            }
            json.Append("}");
            return Encoding.UTF8.GetBytes(json.ToString());
        }

        // 契约测试向量的密钥：口令密文可以解开，闸门之前不会因口令 400。
        static BridgeConfig Cfg(bool enabled)
        {
            BridgeConfig cfg = new BridgeConfig();
            cfg.EncKey = Crypto.EncKey(Crypto.ParseHexLower(SelfTest.SecretHex));
            cfg.AllowedAccounts = new string[] { Listed, Other, ReadOnly };
            cfg.TestAccounts = new string[] { Listed, ReadOnly };
            cfg.ReadOnlyAccounts = new string[] { ReadOnly };
            cfg.EnableReplicatedWrites = enabled;
            return cfg;
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
