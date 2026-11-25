using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;

namespace U8Co
{
    internal static class ProbeParse
    {
        // write_budget_s：写步骤的时间预算（秒，1–3600），Linux 端按看门狗剩余时间算好传进来；没有就是 0，不限。
        public static List<Dictionary<string, object>> Read(string script, out int budget)
        {
            if (script == null || script.Trim().Length == 0)
            {
                throw new BridgeException(400, "bad_request", "脚本为空");
            }
            object raw = Parse(script);
            Dictionary<string, object> root = raw as Dictionary<string, object>;
            if (root == null || !root.ContainsKey("steps"))
            {
                throw new BridgeException(400, "bad_request", "脚本必须是含 steps 数组的对象");
            }
            budget = Budget(root);
            ArrayList list = StepIo.AsList(root["steps"]);
            if (list == null)
            {
                throw new BridgeException(400, "bad_request", "steps 必须是数组");
            }
            return Steps(list);
        }

        static int Budget(Dictionary<string, object> root)
        {
            if (!StepIo.Has(root, "write_budget_s"))
            {
                return 0;
            }
            int value = Nums.AsInt(root["write_budget_s"]);
            if (value < 1 || value > 3600)
            {
                throw new BridgeException(400, "bad_request", "write_budget_s 超出范围");
            }
            return value;
        }

        static object Parse(string script)
        {
            JavaScriptSerializer ser = new JavaScriptSerializer();
            ser.MaxJsonLength = 4 * 1024 * 1024;
            ser.RecursionLimit = 64;
            try
            {
                return ser.DeserializeObject(script);
            }
            catch (Exception)
            {
                throw new BridgeException(400, "bad_request", "脚本不是 JSON");
            }
        }

        static List<Dictionary<string, object>> Steps(ArrayList list)
        {
            List<Dictionary<string, object>> steps = new List<Dictionary<string, object>>();
            for (int i = 0; i < list.Count; i++)
            {
                Dictionary<string, object> step = list[i] as Dictionary<string, object>;
                if (step == null)
                {
                    throw new BridgeException(400, "bad_request", "第 " + (i + 1).ToString(CultureInfo.InvariantCulture) + " 步不是对象");
                }
                if (!Known(StepIo.Op(step)))
                {
                    throw new BridgeException(400, "bad_request", "不认识操作 " + StepIo.Op(step));
                }
                steps.Add(step);
            }
            return steps;
        }

        static bool Known(string op)
        {
            if (KnownCom(op) || KnownDom(op))
            {
                return true;
            }
            return KnownSql(op);
        }

        static bool KnownCom(string op)
        {
            return op == "login" || op == "hold" || op == "create" || op == "call" || op == "get" || op == "set";
        }

        static bool KnownDom(string op)
        {
            return op == "dom.new" || op == "dom.blank" || op == "dom.load" || op == "dom.addrow"
                || op == "dom.rows" || op == "dom.set" || op == "dom.single" || op == "dom.xml";
        }

        static bool KnownSql(string op)
        {
            return op == "sql" || op == "trans.begin" || op == "trans.commit" || op == "trans.rollback"
                || op == "lastsql";
        }
    }

    internal static class ProbeStdin
    {
        public static string ReadScript(out string secret)
        {
            using (StreamReader reader = new StreamReader(Console.OpenStandardInput(), new UTF8Encoding(false)))
            {
                secret = reader.ReadLine();
                return reader.ReadToEnd();
            }
        }
    }
}
