using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;

namespace U8Co
{
    // Dispatch 路由表（Dispatch.Routes）的全部路径，只读副本，给 --selftest 核对写闸门登记用（WriteGateSelfTest）；
    // 另给出写闸门、登录前字段表的登记路径，反向核对它们都在路由表里。
    // 按字段名反射读取，不改 Dispatch；字段改名或类型变了会抛异常，自检随之失败，不会悄悄跳过。
    internal static class DispatchRoutes
    {
        public static List<string> Paths()
        {
            FieldInfo field = typeof(Dispatch).GetField("Routes", BindingFlags.NonPublic | BindingFlags.Static);
            IDictionary map = field == null ? null : field.GetValue(null) as IDictionary;
            if (map == null || map.Count == 0)
            {
                throw new InvalidOperationException("Dispatch 路由表不可枚举");
            }
            List<string> paths = new List<string>();
            foreach (object key in map.Keys)
            {
                paths.Add((string)key);
            }
            paths.Sort(StringComparer.Ordinal);
            return paths;
        }

        // 反向核对用：写闸门登记的全部写路由（WriteGate.Writes）。
        public static List<string> WritePaths()
        {
            FieldInfo field = typeof(WriteGate).GetField("Writes", BindingFlags.NonPublic | BindingFlags.Static);
            IEnumerable set = field == null ? null : field.GetValue(null) as IEnumerable;
            if (set == null)
            {
                throw new InvalidOperationException("写闸门登记表不可枚举");
            }
            List<string> paths = new List<string>();
            foreach (object key in set)
            {
                paths.Add((string)key);
            }
            return paths;
        }

        // 反向核对用：登录前字段表（Requests.P4Specs）里登记的全部路径，每项第一个元素是路径。
        public static List<string> SpecPaths()
        {
            FieldInfo field = typeof(Requests).GetField("P4Specs", BindingFlags.NonPublic | BindingFlags.Static);
            string[][] specs = field == null ? null : field.GetValue(null) as string[][];
            if (specs == null || specs.Length == 0)
            {
                throw new InvalidOperationException("登录前字段表不可枚举");
            }
            List<string> paths = new List<string>();
            foreach (string[] spec in specs)
            {
                paths.Add(spec[0]);
            }
            return paths;
        }
    }
}
