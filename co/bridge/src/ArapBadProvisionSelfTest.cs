using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

namespace U8Co
{
    // --selftest 的计提坏账准备部分（9F）：脚本拒绝编号到中文、内嵌脚本与拒绝表一致、脚本里的关键写法、
    // 计数和账龄区间整形、回读核对、响应字段。只跑纯函数和内嵌资源，不连库、不建 COM。
    internal static class ArapBadProvisionSelfTest
    {
        public static void Run()
        {
            CheckRefusals();
            CheckScript();
            CheckScriptRules();
            CheckShape();
            CheckBuckets();
            CheckHelpers();
        }

        static void CheckRefusals()
        {
            BridgeException closed = ArapBadProvisionSql.Refused(new ScriptRefusal(50161, "AR period already closed (GL_mend)", null), 2026, 9);
            Expect("bad prov closed", closed.Status == 409 && closed.Code == "state_mismatch" && closed.Message == "应收 2026 年 9 月已结账");
            BridgeException none = ArapBadProvisionSql.Refused(new ScriptRefusal(50162, "x", null), 2026, 9);
            Expect("bad prov no para", none.Message == "未设置 2026 年坏账准备参数（应收款管理 › 设置 › 坏账准备）");
            BridgeException zero = ArapBadProvisionSql.Refused(new ScriptRefusal(50166, "x", null), 2026, 9);
            Expect("bad prov zero", zero.Status == 409 && zero.Message == "本次计提金额为 0");
            BridgeException vouched = ArapBadProvisionSql.Refused(new ScriptRefusal(50167, "Ar_BadPara cPZID set: 12", null), 2026, 9);
            Expect("bad prov vouched", vouched.Message == "2026 年的坏账准备计提已制单，请先删除凭证 12");
            BridgeException style = ArapBadProvisionSql.Refused(new ScriptRefusal(50164, "x", null), 2026, 9);
            Expect("bad prov style", style.Status == 409 && style.Message.StartsWith("不支持的坏账计提方法", StringComparison.Ordinal));
            Expect("bad prov internal", ArapBadProvisionSql.Refused(new ScriptRefusal(50160, "x", null), 2026, 9).Status == 500);
            Expect("bad prov unknown", ArapBadProvisionSql.Refused(new ScriptRefusal(50199, "x", null), 2026, 9).Status == 500);
        }

        // 脚本已登记、不分 GO；每个 THROW 编号都在拒绝区间内、并有中文（内部防御除外）。
        static void CheckScript()
        {
            string name = ArapBadProvisionSql.Script;
            Expect("bad prov script listed", Array.IndexOf(SqlScript.Names, name) >= 0);
            string text = SqlScript.Text(name);
            Expect("bad prov script no GO", !SqlScriptSelfTest.HasGo(text));
            Regex thrown = new Regex("THROW (5[0-9]{4})", RegexOptions.CultureInvariant);
            int count = 0;
            foreach (Match m in thrown.Matches(text))
            {
                int number = int.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture);
                Expect("bad prov script range " + number, SqlScript.IsRefusal(number) && number >= 50160 && number <= 50179);
                count++;
                if (number == ArapBadProvisionSql.InternalRefusal)
                {
                    continue;
                }
                Expect("bad prov script text " + number,
                    ArapBadProvisionSql.Refused(new ScriptRefusal(number, "x: 1", null), 2026, 1).Status == 409);
            }
            Expect("bad prov script throws", count >= 10);
            Expect("bad prov prep", ArapBadProvisionSql.PrepSql.Contains("#badp_args")
                && ArapBadProvisionSql.DropSql.Contains("#badp_age") && ArapBadProvisionSql.DropSql.Contains("#badp_bal"));
        }

        // 只更新、不插年度行，不写往来明细；编号 HZ/AR；本次计提 = 目标 − 当前余额。
        static void CheckScriptRules()
        {
            string text = SqlScript.Text(ArapBadProvisionSql.Script);
            Has("bad prov rule", text, "cType = N'HZ' AND cFlag = N'AR'", "N'HZAR' + REPLICATE(N'0', 13 - LEN(",
                "SET @jt = ROUND(ISNULL(@target, 0) - @remain, 2);", "iJtAmount = ISNULL(iJtAmount, 0) + @jt",
                "cProcStyle = N'9F'", "WHERE autoid = @id AND iYear = @y;", "ROUND(bal * rate / 100, 2)",
                "ISNULL(cBusType, N'') = N''", "b.dDate >= @dStart AND b.dDate <= @dReg");
            Expect("bad prov rule no insert", !text.Contains("INSERT INTO Ar_BadPara") && !text.Contains("INSERT INTO Ar_Detail")
                && !text.Contains("FormatXML ="));
        }

        static void CheckShape()
        {
            ScriptResult script = Script("1", new string[] { "120000.00", "0.0100000000", "1200.00", "1000.00", "200.00", "1200.00" });
            ProvisionResult r = ArapBadProvision.Shape(script);
            Expect("bad prov shape method", r.Method == 1 && r.Buckets.Count == 0);
            Expect("bad prov shape base", r.Base == 120000m && r.Rate == 0.01m && r.Target == 1200m);
            Expect("bad prov shape remain", r.RemainBefore == 1000m && r.Amount == 200m && r.RemainAfter == 1200m);
            Expect("bad prov shape no", r.CancelNo == "HZAR0000000000014" && r.RowId == 3);
            Expect("bad prov rate text", r.Rate.ToString(CultureInfo.InvariantCulture) == "0.01");
            Dictionary<string, object> body = ArapBadProvision.Body(null, Args(), r, false);
            Expect("bad prov body style", (string)body["style"] == "9F" && (string)body["action"] == "provision");
            Expect("bad prov body rate", (decimal)body["rate"] == 0.01m && !body.ContainsKey("buckets"));
            Expect("bad prov body amount", (decimal)body["amount"] == 200m && !(bool)body["dry_run"]);
            Expect("bad prov body method", (string)body["method_name"] == "应收余额百分比法");
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["no"] = "HZAR0000000000014";
            row["remain"] = "1200.0000";
            Expect("bad prov reread ok", ArapBadProvision.Matches(row, r));
            row["remain"] = "1000.00";
            Expect("bad prov reread bad", !ArapBadProvision.Matches(row, r));
            Expect("bad prov reread none", !ArapBadProvision.Matches(null, r));
        }

        static void CheckBuckets()
        {
            ScriptResult script = Script("2", new string[] { "50000.00", "0.0000000000", "3500.00", "4000.00", "-500.00", "3500.00" });
            script.Rows.Add(AgeRow("0", "365", "40000.00", "5.0000000000", "2000.00"));
            script.Rows.Add(AgeRow("366", null, "10000.00", "15.0000000000", "1500.00"));
            ProvisionResult r = ArapBadProvision.Shape(script);
            Expect("bad prov buckets count", r.Method == 2 && r.Buckets.Count == 2 && r.Amount == -500m);
            Expect("bad prov bucket first", (int)r.Buckets[0]["to"] == 365 && (decimal)r.Buckets[0]["amount"] == 2000m);
            Expect("bad prov bucket open", r.Buckets[1]["to"] == null && (int)r.Buckets[1]["from"] == 366);
            Expect("bad prov bucket rate", (decimal)r.Buckets[1]["rate"] == 15m);
            Dictionary<string, object> body = ArapBadProvision.Body(null, Args(), r, true);
            Expect("bad prov body age", body.ContainsKey("buckets") && !body.ContainsKey("rate") && (bool)body["dry_run"]);
            Expect("bad prov body age name", (string)body["method_name"] == "账龄分析法");
        }

        static void CheckHelpers()
        {
            Expect("bad prov payday", ArapBadProvisionSql.IsOn("1") && ArapBadProvisionSql.IsOn(" True ")
                && !ArapBadProvisionSql.IsOn("0") && !ArapBadProvisionSql.IsOn(null));
            Expect("bad prov method", ArapBadProvisionSql.MethodName(3) == "销售收入百分比法" && ArapBadProvisionSql.MethodName(4) == "");
            Expect("bad prov trim", ArapBadProvisionSql.Trim(5.0000000000m).ToString(CultureInfo.InvariantCulture) == "5");
        }

        static ProvisionArgs Args()
        {
            ProvisionArgs args = new ProvisionArgs();
            args.Date = "2026-09-30";
            args.Year = 2026;
            args.Period = 9;
            return args;
        }

        // values：base、rate、target、remain_before、jt、remain_after。
        static ScriptResult Script(string style, string[] values)
        {
            string[] keys = new string[] { "base", "rate", "target", "remain_before", "jt", "remain_after" };
            ScriptResult script = new ScriptResult();
            script.HasCounts = true;
            script.Rows = new List<Dictionary<string, object>>();
            script.Counts["style"] = style;
            for (int i = 0; i < keys.Length; i++)
            {
                script.Counts[keys[i]] = values[i];
            }
            script.Counts["cancel_no"] = "HZAR0000000000014";
            script.Counts["row_id"] = "3";
            return script;
        }

        static Dictionary<string, object> AgeRow(string from, string to, string balance, string rate, string amount)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["bucket_from"] = from;
            if (to != null)
            {
                row["bucket_to"] = to;
            }
            row["balance"] = balance;
            row["rate"] = rate;
            row["amount"] = amount;
            return row;
        }

        static void Has(string name, string text, params string[] parts)
        {
            foreach (string part in parts)
            {
                Expect(name + " " + part, text.Contains(part));
            }
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
