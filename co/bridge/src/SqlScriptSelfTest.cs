using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的内嵌脚本部分（存货核算）：名单里的脚本都已嵌入、可读、非空，程序集里没有名单外的 sql/ 资源，
    // 脚本里没有 GO 行（整批执行，GO 会让服务器报语法错误）。只读资源和纯函数，不连库、不建 COM。
    internal static class SqlScriptSelfTest
    {
        public static void Run()
        {
            CheckResources();
            CheckTexts();
            CheckMissing();
            CheckShapes();
        }

        static void CheckResources()
        {
            HashSet<string> wanted = new HashSet<string>(SqlScript.Names, StringComparer.Ordinal);
            int found = 0;
            foreach (string name in typeof(SqlScript).Assembly.GetManifestResourceNames())
            {
                if (!name.StartsWith("sql/", StringComparison.Ordinal))
                {
                    continue;
                }
                Expect("sql resource listed " + name, wanted.Contains(name));
                Expect("sql resource depth " + name, name.Split('/').Length == 3);
                found++;
            }
            Expect("sql resources count", found == SqlScript.Names.Length);
        }

        static void CheckTexts()
        {
            foreach (string name in SqlScript.Names)
            {
                string text = SqlScript.Text(name);
                Expect("sql text " + name, text.Trim().Length > 0);
                Expect("sql cached " + name, object.ReferenceEquals(text, SqlScript.Text(name)));
                Expect("sql no GO " + name, !HasGo(text));
            }
        }

        internal static bool HasGo(string text)
        {
            foreach (string line in text.Split('\n'))
            {
                if (string.Equals(line.Trim(), "GO", StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }

        static void CheckMissing()
        {
            bool refused = false;
            try
            {
                SqlScript.Text("sql/ia/none.sql");
            }
            catch (BridgeException ex)
            {
                refused = ex.Status == 500;
            }
            Expect("sql missing 500", refused);
            Expect("sql go line", HasGo("SELECT 1\r\n go \r\nSELECT 2"));
            Expect("sql go word", !HasGo("-- GO first\nSELECT 1 AS go_on"));
        }

        static void CheckShapes()
        {
            Expect("sql counts shape", SqlScript.IsCounts(new List<string> { "k", "n" }));
            Expect("sql counts case", SqlScript.IsCounts(new List<string> { "K", "N" }));
            Expect("sql counts extra", !SqlScript.IsCounts(new List<string> { "k", "n", "x" }));
            Expect("sql counts order", !SqlScript.IsCounts(new List<string> { "n", "k" }));
            Expect("sql refusal low", SqlScript.IsRefusal(50000) && !SqlScript.IsRefusal(49999));
            Expect("sql refusal high", SqlScript.IsRefusal(50099) && !SqlScript.IsRefusal(50100));
            Expect("sql refusal qc", SqlScript.IsRefusal(50101) && SqlScript.IsRefusal(50116)
                && SqlScript.IsRefusal(50199) && !SqlScript.IsRefusal(50200));
            Expect("sql detail column", SqlScript.HasColumn(new List<string> { "cWhDepCode", "CINVCODE" }, "cInvCode"));
            Expect("sql detail other", !SqlScript.HasColumn(new List<string> { "step", "note" }, "cInvCode"));
            CheckRequire();
            Expect("sql args fill", SqlScript.ArgsFill.Split('?').Length == 6);
            Expect("sql args drop first", SqlScript.ArgsTable.IndexOf("DROP TABLE #ia_args", StringComparison.Ordinal)
                < SqlScript.ArgsTable.IndexOf("CREATE TABLE #ia_args", StringComparison.Ordinal));
        }

        static void CheckRequire()
        {
            ScriptResult result = new ScriptResult();
            Expect("sql require none", Throws(result, null));
            result.HasCounts = true;
            Expect("sql require any", !Throws(result, null));
            Expect("sql require missing", Throws(result, "subsidiary_month"));
            result.Counts["subsidiary_month"] = "3";
            Expect("sql require key", !Throws(result, "subsidiary_month"));
            Expect("sql refusal rows", new ScriptRefusal(50061, "x", null).Rows.Count == 0);
        }

        static bool Throws(ScriptResult result, string key)
        {
            try
            {
                result.Require(key);
                return false;
            }
            catch (BridgeException ex)
            {
                return ex.Status == 500;
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
