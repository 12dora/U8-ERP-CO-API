using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // vouchers/load_many 的请求：type + ids（不重复的正整数，1 到 20 个）。
    // 经 U8 COM 读取的类型（不在 RouteClass 的 SQL 读取名单里）一张一次 COM 调用，最多 5 张，控制时长和许可占用。
    // 登录前（Requests.ApplyP4）、排程取锁键（DocLocks）和处理函数里各解析一次，都是纯校验。
    internal static class LoadManyReq
    {
        internal const int MaxSql = 20;
        internal const int MaxCom = 5;
        internal const string ComHint = "该类型逐张用 COM 读取，一次最多 5 张";

        public static int[] Parse(VoucherKind kind, Dictionary<string, object> body)
        {
            if (kind == null)
            {
                throw BridgeException.BadField("type", "缺少单据类型");
            }
            IList list = IdList(body);
            if (list.Count > MaxCom && !RouteClass.IsSqlLoad(kind))
            {
                throw BridgeException.BadField("ids", ComHint).WithHint(ComHint);
            }
            int[] ids = new int[list.Count];
            HashSet<int> seen = new HashSet<int>();
            for (int i = 0; i < list.Count; i++)
            {
                string at = FieldPath.Item("ids", i);
                ids[i] = Id(list[i], at);
                if (!seen.Add(ids[i]))
                {
                    throw BridgeException.BadField(at, "ids 有重复");
                }
            }
            return ids;
        }

        static IList IdList(Dictionary<string, object> body)
        {
            object raw = Requests.Field(body, "ids");
            if (raw == null)
            {
                throw BridgeException.BadField("ids", "缺少字段 ids");
            }
            IList list = raw as IList;
            if (list == null || raw is string)
            {
                throw BridgeException.BadField("ids", "ids 必须是 JSON 数组");
            }
            if (list.Count < 1 || list.Count > MaxSql)
            {
                throw BridgeException.BadField("ids", "ids 必须是 1 到 20 个");
            }
            return list;
        }

        static int Id(object value, string at)
        {
            long number = value is int ? (int)value : value is long ? (long)value : long.MinValue;
            if (number == long.MinValue)
            {
                throw BridgeException.BadField(at, "单据 id 必须是整数");
            }
            if (number < 1 || number > int.MaxValue)
            {
                throw BridgeException.BadField(at, "单据 id 无效");
            }
            return (int)number;
        }

        // 与单张 vouchers/load 一样逐张锁 "<类型>:<id>"（写线程池上排程用；命中登录缓存进读线程池时 StaWorker 清空锁键）。
        // 请求不合法时返回空数组（登录前已按 Parse 拒绝，这里只防御）。
        public static string[] LockKeys(WorkItem item)
        {
            if (item == null || item.Type == null || item.Type.Name == null)
            {
                return new string[0];
            }
            int[] ids;
            try
            {
                ids = Parse(item.Type, item.Body);
            }
            catch (BridgeException)
            {
                return new string[0];
            }
            string[] keys = new string[ids.Length];
            for (int i = 0; i < ids.Length; i++)
            {
                keys[i] = item.Type.Name + ":" + ids[i].ToString(CultureInfo.InvariantCulture);
            }
            return keys;
        }

        // 审计 detail：单据 id 列表（CoRows.Note 截到 300 字）。
        public static void Note(WorkItem item, int[] ids)
        {
            StringBuilder sb = new StringBuilder("单据 ");
            for (int i = 0; i < ids.Length; i++)
            {
                if (i > 0)
                {
                    sb.Append(',');
                }
                sb.Append(ids[i].ToString(CultureInfo.InvariantCulture));
            }
            CoRows.Note(item, sb.ToString());
        }
    }
}
