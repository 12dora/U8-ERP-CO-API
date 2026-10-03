using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace U8Co
{
    // --selftest 的汇兑损益部分：请求校验、锁键、汇率选择与文本、脚本拒绝编号到中文、回读 SQL、结果整形、
    // 内嵌脚本与拒绝表一致、预演模式和权限规则已登记。只跑纯函数和内嵌资源，不连库、不建 COM。
    internal static class ArapExGainSelfTest
    {
        // 脚本里只用于防御的编号（参数缺失、工作表缺失），按内部错误处理，不进中文表。
        static readonly int[] Internal = new int[] { 50001, 50002, 50029 };

        public static void Run()
        {
            CheckCreateParse();
            CheckCreateRefuse();
            CheckCancelParse();
            CheckRate();
            CheckRefusals();
            CheckScripts();
            CheckSigns();
            CheckScriptRules();
            CheckShape();
            CheckWiring();
        }

        static void CheckCreateParse()
        {
            Dictionary<string, object> body = Body("AR");
            body["currency"] = " 美元 ";
            ExGainAsk ask = ArapExGainReq.Parse(ArapExGainReq.Path, body);
            Expect("exgain parse head", !ask.Cancel && ask.Flag == "AR" && ask.Date == "2026-08-31" && ask.Currency == "美元");
            Expect("exgain parse defaults", ask.Rate == 0m && ask.Partners.Count == 0 && ask.SettleCleared);
            CheckLockKeys(body);
            body["rate"] = 7.1234m;
            body["partners"] = new List<object> { "C0001", " C0004 " };
            body["settle_cleared"] = false;
            ask = ArapExGainReq.Parse(ArapExGainReq.Path, body);
            Expect("exgain parse full", ask.Rate == 7.1234m && ask.Partners.Count == 2 && ask.Partners[1] == "C0004"
                && !ask.SettleCleared);
            Expect("exgain title", ArapExGainReq.Title(ask) == "应收汇兑损益 美元 2026-08-31");
        }

        static void CheckLockKeys(Dictionary<string, object> good)
        {
            string[] keys = ArapExGainReq.LockKeys(ArapExGainReq.Path, good);
            Expect("exgain lock keys", keys.Length == 2 && keys[0] == "arap:writeoff:AR" && keys[1] == "arap:exgain:AR");
            Dictionary<string, object> bad = Body("XX");
            Expect("exgain lock keys bad", ArapExGainReq.LockKeys(ArapExGainReq.Path, bad).Length == 0);
        }

        static void CheckCreateRefuse()
        {
            Refuse("exgain flag", ArapExGainReq.Path, "flag", "AX", "flag");
            Refuse("exgain currency", ArapExGainReq.Path, "currency", " ", "currency");
            Refuse("exgain rate zero", ArapExGainReq.Path, "rate", 0, "rate");
            Refuse("exgain rate text", ArapExGainReq.Path, "rate", "7.12", "rate");
            Refuse("exgain rate scale", ArapExGainReq.Path, "rate", 7.12340000001m, "rate");
            Refuse("exgain settle", ArapExGainReq.Path, "settle_cleared", "yes", "settle_cleared");
            Refuse("exgain partners empty", ArapExGainReq.Path, "partners", new List<object>(), "partners");
            Refuse("exgain partners dup", ArapExGainReq.Path, "partners", new List<object> { "C1", "C1" }, "partners.1");
            Refuse("exgain partners blank", ArapExGainReq.Path, "partners", new List<object> { "C1", 5 }, "partners.1");
            Expect("exgain rate max", ArapExGainReq.Rate(1000000) == 1000000m);
        }

        static void CheckCancelParse()
        {
            Dictionary<string, object> body = Body("AR");
            ExGainAsk ask = ArapExGainReq.Parse(ArapExGainReq.CancelPath, body);
            Expect("exgain cancel by date", ask.Cancel && ask.CancelNos.Count == 0 && ask.Date == "2026-08-31"
                && ArapExGainReq.Title(ask) == "应收取消汇兑损益 2026-08-31");
            body["cancel_nos"] = new List<object> { "SYRAR0000900001", "SYRAR0000900002" };
            ask = ArapExGainReq.Parse(ArapExGainReq.CancelPath, body);
            Expect("exgain cancel nos", ask.CancelNos.Count == 2 && ArapExGainReq.Title(ask) == "应收取消汇兑损益 SYRAR0000900001 等 2 个");
            Refuse("exgain cancel side", ArapExGainReq.CancelPath, "cancel_nos", new List<object> { "SYPAP0000000001" }, "cancel_nos.0");
            Refuse("exgain cancel prefix", ArapExGainReq.CancelPath, "cancel_nos", new List<object> { "HXAR0000000001" }, "cancel_nos.0");
            Refuse("exgain cancel dup", ArapExGainReq.CancelPath, "cancel_nos",
                new List<object> { "SYRAR0000000001", "SYRAR0000000001" }, "cancel_nos.1");
            List<object> many = new List<object>();
            for (int i = 0; i <= ArapExGainReq.MaxList; i++)
            {
                many.Add("SYRAR" + (1000 + i).ToString(System.Globalization.CultureInfo.InvariantCulture));
            }
            Refuse("exgain cancel many", ArapExGainReq.CancelPath, "cancel_nos", many, "cancel_nos");
            Dictionary<string, object> ap = Body("AP");
            ap["cancel_nos"] = new List<object> { "SYPAP0000000001" };
            Expect("exgain cancel ap", ArapExGainReq.Parse(ArapExGainReq.CancelPath, ap).CancelNos[0] == "SYPAP0000000001");
        }

        // 本期调整汇率（iType=3）取样例 7.1234；调用方给的汇率优先；都没有 409。
        static void CheckRate()
        {
            Expect("exgain rate asked", ArapExGainSql.PickRate(7.1m, 7.1234m, "美元") == 7.1m);
            Expect("exgain rate adjust", ArapExGainSql.PickRate(0m, 7.1234m, "美元") == 7.1234m);
            bool refused = false;
            try
            {
                ArapExGainSql.PickRate(0m, 0m, "美元");
            }
            catch (BridgeException ex)
            {
                refused = ex.Status == 409 && ex.Message.Contains("本期没有调整汇率");
            }
            Expect("exgain rate none", refused);
            Expect("exgain rate text", ArapExGainSql.RateText(7.1234000000m) == "7.1234"
                && ArapExGainSql.RateText(7.0288m) == "7.0288" && ArapExGainSql.RateText(1m) == "1");
        }

        static void CheckRefusals()
        {
            BridgeException missing = ArapExGainSql.Refused(new ScriptRefusal(50025, "9M batch not found: SYRAR0000000002", null));
            Expect("exgain refuse missing", missing.Status == 404 && missing.Message.Contains("SYRAR0000000002"));
            BridgeException vouched = ArapExGainSql.Refused(
                new ScriptRefusal(50026, "9M cPZid set, CancelAccVouch first: SYRAR0000000003", null));
            Expect("exgain refuse vouched", vouched.Status == 409 && vouched.Code == "state_mismatch"
                && vouched.Message == "汇兑损益已制单，请先删除凭证：SYRAR0000000003");
            BridgeException none = ArapExGainSql.Refused(new ScriptRefusal(50023, "no 9M rows (iDiffAmount all 0)", null));
            Expect("exgain refuse none", none.Status == 409 && none.Message == "按该汇率计算，没有需要调整的汇兑损益");
            Expect("exgain refuse unknown", ArapExGainSql.Refused(new ScriptRefusal(50099, "x", null)).Status == 500
                && ArapExGainSql.Refused(new ScriptRefusal(50024, "x", null)).Status == 500);
            BridgeException closed = ArapExGainSql.Refused(
                new ScriptRefusal(50035, "AR/AP period already closed (GL_mend): SYRAR0000000004", null));
            Expect("exgain refuse cancel closed", closed.Status == 409 && closed.Message == "该期间已结账，不能取消汇兑损益：SYRAR0000000004");
            Expect("exgain tail", ArapExGainSql.TailOf("a: b: SYPAP1 ") == "SYPAP1" && ArapExGainSql.TailOf("none") == "");
            string sql = ArapExGainSql.CountSql("AP", 3);
            Expect("exgain count sql", sql.EndsWith("from Ap_Detail where cProcStyle=N'9M' and cFlag=? and cCancelNo in (?,?,?)",
                StringComparison.Ordinal));
        }

        // 四段脚本都在 SqlScript 名单里、没有 GO；脚本里每个 THROW 编号（防御性的除外）都有中文（404 / 409）。
        static void CheckScripts()
        {
            string[] names = new string[]
            {
                ArapExGainSql.CreateScript, ArapExGainSql.PersistScript, ArapExGainSql.CancelScript, ArapExGainSql.CancelEndScript
            };
            Regex thrown = new Regex("THROW (5[0-9]{4})", RegexOptions.CultureInvariant);
            foreach (string name in names)
            {
                Expect("exgain script listed " + name, Array.IndexOf(SqlScript.Names, name) >= 0);
                string text = SqlScript.Text(name);
                Expect("exgain script no GO " + name, !SqlScriptSelfTest.HasGo(text));
                foreach (Match m in thrown.Matches(text))
                {
                    int number = int.Parse(m.Groups[1].Value, System.Globalization.CultureInfo.InvariantCulture);
                    Expect("exgain script range " + number, SqlScript.IsRefusal(number));
                    if (Array.IndexOf(Internal, number) >= 0)
                    {
                        continue;
                    }
                    int status = ArapExGainSql.Refused(new ScriptRefusal(number, "x: SYRAR1", null)).Status;
                    Expect("exgain script text " + number, status == 409 || status == 404);
                }
            }
            Expect("exgain prep tables", ArapExGainSql.PrepSql.Contains("#exg_args") && ArapExGainSql.PrepSql.Contains(ArapExGainSql.SaleTable)
                && ArapExGainSql.PrepSql.Contains(ArapExGainSql.PurTable));
        }

        // 应收单 / 应付单表头余额：新增与取消必须互逆（两个方向、两侧）。下面两个函数照两段脚本的写法，
        // CheckScriptRules 核对脚本里确实是这几句。正常方向的单据新增仍是 +差额（同 U8 FrmHdsy）。
        static void CheckSigns()
        {
            string[] flags = new string[] { "AR", "AP" };
            decimal[] diffs = new decimal[] { -5.00m, 41.80m };
            foreach (string flag in flags)
            {
                for (int bdC = 0; bdC <= 1; bdC++)
                {
                    foreach (decimal diff in diffs)
                    {
                        string name = "exgain sign " + flag + " bd_c=" + bdC + " " + diff;
                        Expect(name, CreateDelta(flag, bdC, diff) + CancelDelta(flag, bdC, diff) == 0m);
                    }
                }
            }
            Expect("exgain sign normal", CreateDelta("AR", 1, -5m) == -5m && CreateDelta("AP", 0, -5m) == -5m);
            Expect("exgain sign reverse", CreateDelta("AR", 0, -5m) == 5m && CreateDelta("AP", 1, -5m) == 5m);
        }

        // exgain_create.sql：amt = 差额；v.bd_c = @flip（应收 0、应付 1）时取反。
        static decimal CreateDelta(string flag, int bdC, decimal diff)
        {
            int flip = flag == "AR" ? 0 : 1;
            return bdC == flip ? -diff : diff;
        }

        // exgain_cancel.sql：差额存在借方（应收 R*）或贷方（应付 P*），IJE = −(借 + 贷)；应收 bd_c=0、应付 bd_c=1 时取反。
        static decimal CancelDelta(string flag, int bdC, decimal diff)
        {
            decimal debit = flag == "AR" ? diff : 0m;
            decimal credit = flag == "AR" ? 0m : diff;
            decimal ije = -(debit + credit);
            if ((flag == "AR" && bdC == 0) || (flag == "AP" && bdC == 1))
            {
                ije = -ije;
            }
            return ije;
        }

        static void CheckScriptRules()
        {
            string create = SqlScript.Text(ArapExGainSql.CreateScript);
            string persist = SqlScript.Text(ArapExGainSql.PersistScript);
            string cancel = SqlScript.Text(ArapExGainSql.CancelScript);
            const string Diff = "SUM(iDAmount) - SUM(iCAmount)";
            Has("exgain rule create", create, "SET @flip = CASE WHEN @flag = N'AR' THEN 0 ELSE 1 END;",
                "CASE WHEN v.bd_c = @flip THEN -t.amt ELSE t.amt END",
                "SET iDAmount = iAmount WHERE cVouchType IN (N'26', N'27') OR cVouchType LIKE N'R[0-9]';",
                "SET iCAmount = iAmount WHERE cVouchType IN (N'01', N'02') OR cVouchType LIKE N'P[0-9]';",
                "ORDER BY iDiffAmount DESC, rid", "ISNULL(ID, 0) <= 0");
            Has("exgain rule cancel", cancel, "IJE = -(iDAmount + iCAmount)",
                "WHERE (cFlag = N'AR' AND bd_c = 0) OR (cFlag = N'AP' AND bd_c = 1);", "AR_RZDetail", "THROW 50035", Diff);
            Has("exgain rule persist", persist, Diff);
            Has("exgain drop temps", ArapExGainSql.DropSql, "#exg_skip", "#exg_odd");
            Expect("exgain rule rows", Occurs(create, "THROW 50033") == 3 && !create.Contains("THROW 50024"));
            Expect("exgain rule odd", Occurs(cancel, "THROW 50032") == 2 && !cancel.Contains("THROW 50020"));
            Expect("exgain rule diff", !persist.Contains("SUM(iAmount)"));
        }

        static void Has(string name, string text, params string[] parts)
        {
            foreach (string part in parts)
            {
                Expect(name + " " + part, text.Contains(part));
            }
        }

        static int Occurs(string text, string part)
        {
            int count = 0;
            int at = text.IndexOf(part, StringComparison.Ordinal);
            while (at >= 0)
            {
                count++;
                at = text.IndexOf(part, at + part.Length, StringComparison.Ordinal);
            }
            return count;
        }

        static void CheckShape()
        {
            ScriptResult script = new ScriptResult();
            script.Rows = new List<Dictionary<string, object>>();
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["cancel_no"] = "SYRAR0000900002";
            row["partner"] = "C0004";
            row["vtype"] = "27";
            row["vid"] = "XS0000000002";
            row["lines"] = "2";
            row["diff"] = "-1.42";
            script.Rows.Add(row);
            script.Counts["total"] = "-1.42";
            script.Counts["inserted"] = "2";
            ExGainResult result = new ExGainResult();
            ArapExGain.Shape(result, script);
            Dictionary<string, object> one = result.Batches[0];
            Expect("exgain shape", result.Nos.Count == 1 && result.Nos[0] == "SYRAR0000900002" && result.Total == -1.42m
                && (string)one["type"] == "27" && (string)one["id"] == "XS0000000002" && (int)one["lines"] == 2
                && (decimal)one["diff"] == -1.42m && (string)one["partner"] == "C0004");
            Expect("exgain count", ArapExGain.Count(script, "inserted") == 2 && ArapExGain.Count(script, "none") == 0);
        }

        static void CheckWiring()
        {
            Expect("exgain dry run", DryRunModes.Lookup("arap/exchange_gain", "", "", "") == DryRunModes.Rollback
                && DryRunModes.Lookup("/u8co/v1/arap/exchange_gain/cancel", "", "", "") == DryRunModes.Rollback);
            Expect("exgain perm", PermRegistry.ForKey(PermRegistry.ExGainKey("AR", false)) != null
                && PermRegistry.ForKey(PermRegistry.ExGainKey("AP", true)) != null
                && PermRegistry.ExGainKey("AP", true) == "write:arap:exchange_gain_cancel:ap");
            Expect("exgain paths", ArapExGainReq.IsPath(ArapExGainReq.Path) && ArapExGainReq.IsPath(ArapExGainReq.CancelPath)
                && !ArapExGainReq.IsPath(Requests.WriteoffCancelPath)
                && ArapExGainReq.ActionOf(ArapExGainReq.CancelPath) == "exchange_gain_cancel");
        }

        static Dictionary<string, object> Body(string flag)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["flag"] = flag;
            body["date"] = "2026-08-31";
            body["currency"] = "美元";
            return body;
        }

        static void Refuse(string name, string path, string key, object value, string field)
        {
            Dictionary<string, object> body = Body("AR");
            body[key] = value;
            bool ok = false;
            try
            {
                ArapExGainReq.Parse(path, body);
            }
            catch (BridgeException ex)
            {
                ok = ex.Status == 400 && ex.Field == field;
            }
            Expect(name, ok);
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
