using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 请求公共字段的校验（从 Requests.cs 移出，行为不变）：单据 id、审核 / 关闭的 action、操作员编码、日期、定长数字。
    internal static partial class Requests
    {
        static int NeedId(Dictionary<string, object> body)
        {
            if (!body.ContainsKey("id"))
            {
                throw BridgeException.BadField("id", "缺少单据 id");
            }
            object value = body["id"];
            if (value is int)
            {
                return Positive((int)value);
            }
            if (value is long)
            {
                long number = (long)value;
                if (number < 1 || number > int.MaxValue)
                {
                    throw BridgeException.BadField("id", "单据 id 无效");
                }
                return (int)number;
            }
            throw BridgeException.BadField("id", "单据 id 必须是整数");
        }

        static int Positive(int id)
        {
            if (id < 1)
            {
                throw BridgeException.BadField("id", "单据 id 无效");
            }
            return id;
        }

        static string NeedAction(Dictionary<string, object> body)
        {
            string action = Need(body, "action");
            if (action != "verify" && action != "unverify")
            {
                throw BridgeException.BadField("action", "action 只能是 verify 或 unverify");
            }
            return action;
        }

        static string NeedClose(Dictionary<string, object> body)
        {
            string action = Need(body, "action");
            if (action != "close" && action != "open")
            {
                throw BridgeException.BadField("action", "action 只能是 close 或 open");
            }
            return action;
        }

        static string CheckOperator(string user)
        {
            if (user.Length == 0 || user.Length > 20)
            {
                throw BridgeException.BadField("operator", "操作员编码无效");
            }
            for (int i = 0; i < user.Length; i++)
            {
                if (user[i] <= ' ' || user[i] == '\'' || user[i] == ';')
                {
                    throw BridgeException.BadField("operator", "操作员编码无效");
                }
            }
            return user;
        }

        static void CheckDate(string date)
        {
            DateTime parsed;
            if (!DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out parsed))
            {
                throw BridgeException.BadField("date", "日期必须是 yyyy-MM-dd");
            }
        }

        static bool Digits(string text, int size)
        {
            if (text == null || text.Length != size)
            {
                return false;
            }
            for (int i = 0; i < text.Length; i++)
            {
                if (text[i] < '0' || text[i] > '9')
                {
                    return false;
                }
            }
            return true;
        }
    }
}
