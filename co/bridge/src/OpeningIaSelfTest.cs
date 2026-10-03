using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // --selftest 的存货核算期初记账部分（openings/post module=ia，OpeningIa）：拒绝编号与内嵌脚本的 THROW 原文一致、
    // 计数键在脚本里、编号映射、登录年度、脚本参数、回读比对。只读内嵌资源和纯函数，不连库、不建 COM。
    internal static class OpeningIaSelfTest
    {
        public static void Run()
        {
            CheckScripts();
            CheckRefusal();
            CheckArgs();
            CheckDrift();
        }

        static void CheckScripts()
        {
            string keep = SqlScript.Text(OpeningIa.KeepScript);
            string recover = SqlScript.Text(OpeningIa.RecoverScript);
            foreach (KeyValuePair<int, string> pair in OpeningIa.Texts)
            {
                string script = pair.Key < 50110 ? keep : recover;
                string line = "THROW " + pair.Key.ToString(CultureInfo.InvariantCulture) + ", N'" + pair.Value + "'";
                Expect("opening ia text " + pair.Key.ToString(CultureInfo.InvariantCulture), script.Contains(line));
            }
            Expect("opening ia throws keep", Throws(keep).TrueForAll(OpeningIa.Texts.ContainsKey) && Throws(keep).Count == 6);
            Expect("opening ia throws recover", Throws(recover).TrueForAll(OpeningIa.Texts.ContainsKey) && Throws(recover).Count == 6);
            foreach (string key in new string[] { OpeningIa.KeepKey, OpeningIa.FifoKey, OpeningIa.FlagKey })
            {
                Expect("opening ia keep key " + key, keep.Contains("N'" + key + "'"));
            }
            foreach (string key in new string[] { OpeningIa.RecoverKey, OpeningIa.FlagKey })
            {
                Expect("opening ia recover key " + key, recover.Contains("N'" + key + "'"));
            }
            Expect("opening ia listed", Array.IndexOf(SqlScript.Names, OpeningIa.KeepScript) >= 0
                && Array.IndexOf(SqlScript.Names, OpeningIa.RecoverScript) >= 0);
        }

        // 脚本里 THROW 的编号（每行最多一个）。
        static List<int> Throws(string script)
        {
            List<int> numbers = new List<int>();
            foreach (string raw in script.Split('\n'))
            {
                string line = raw.Trim();
                if (!line.StartsWith("THROW ", StringComparison.Ordinal))
                {
                    continue;
                }
                int comma = line.IndexOf(',');
                int number;
                if (comma > 6 && int.TryParse(line.Substring(6, comma - 6), NumberStyles.Integer, CultureInfo.InvariantCulture, out number))
                {
                    numbers.Add(number);
                }
            }
            return numbers;
        }

        static void CheckRefusal()
        {
            BridgeException closed = OpeningIa.Refusal(50115);
            Expect("opening ia 50115", closed.Status == 409 && closed.Code == "state_mismatch"
                && closed.Message == "存货核算已有月份结账，不能取消期初记账");
            Expect("opening ia 50104", OpeningIa.Refusal(50104).Message == "存货核算期初已记账");
            BridgeException other = OpeningIa.Refusal(50150);
            Expect("opening ia unknown", other.Status == 409 && other.Code == "state_mismatch" && other.Message.Contains("50150"));
            foreach (int number in OpeningIa.Texts.Keys)
            {
                Expect("opening ia in range " + number.ToString(CultureInfo.InvariantCulture), SqlScript.IsRefusal(number));
            }
        }

        static void CheckArgs()
        {
            Expect("opening ia login ok", OpeningIa.LoginRefusal(2024, 2024, "2024-01-01") == null);
            string wrong = OpeningIa.LoginRefusal(2026, 2024, "2024-01-01");
            Expect("opening ia login year", wrong != null && wrong.Contains("2024-01-01") && wrong.Contains("2026"));
            IaArgs args = OpeningIa.Args(2024, "2024-12-01", "张三");
            Expect("opening ia args", args.Year == 2024 && args.Month == 12 && args.KeepDate == "2024-12-01"
                && args.Accounter == "张三" && args.OnUncosted == "skip");
        }

        static void CheckDrift()
        {
            Dictionary<string, object> keep = new Dictionary<string, object>();
            keep[OpeningIa.KeepKey] = 2L;
            keep[OpeningIa.FlagKey] = 1L;
            Expect("opening ia count", OpeningIa.Count(keep, OpeningIa.KeepKey) == 2 && OpeningIa.Count(keep, "x") == -1);
            Expect("opening ia drift post ok", OpeningIa.Drift(true, keep, 1, 2) == null);
            Expect("opening ia drift post flag", OpeningIa.Drift(true, keep, 0, 2) == "bflag_IA=0");
            Expect("opening ia drift post rows", OpeningIa.Drift(true, keep, 1, 3) == "summary_m0=3");
            Expect("opening ia drift unpost ok", OpeningIa.Drift(false, new Dictionary<string, object>(), 0, 0) == null);
            Expect("opening ia drift unpost left", OpeningIa.Drift(false, null, 0, 1) == "summary_m0=1");
            Expect("opening ia drift unpost flag", OpeningIa.Drift(false, null, 1, 0) == "bflag_IA=1");
            OpeningAsk ask = new OpeningAsk();
            ask.Module = "ia";
            ask.Post = true;
            Dictionary<string, object> body = OpeningIa.Body(ask, 2024, "2024-01-01", keep);
            Expect("opening ia body", (string)body["module"] == "ia" && (string)body["action"] == "post" && (bool)body["posted"]
                && (int)body["opening_year"] == 2024 && (string)body["start_date"] == "2024-01-01"
                && object.ReferenceEquals(body["counts"], keep));
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
