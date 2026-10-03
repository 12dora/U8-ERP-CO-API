using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 字段权限遮蔽：读路由成功（200）之后统一做一次（StaExec），把操作员在 U8 里无权查看的字段置为 null，
    // 响应体加 masked_fields（本次响应里出现并被置空的字段名，排序去重；没有则不加）。只遮不删：行、单据照给，不整张 403。
    // 范围见 PermColumnMap.Plan；perm/snapshot、perm/evaluate、总账、档案和写路由不遮。主管、没有字段权限的操作员不动响应。
    // 审计只记个数，不记字段清单。
    internal static class PermMask
    {
        internal const string Key = "masked_fields";

        sealed class Rule
        {
            public readonly HashSet<string> Denied = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            public bool Group;
        }

        public static void Apply(WorkContext ctx, ApiResult result)
        {
            if (ctx == null || ctx.Item == null || result == null || result.Status != 200 || result.Body == null
                || !PermRegistry.IsRead(ctx.Item.Path))
            {
                return;
            }
            List<ColScope> plan = PermColumnMap.Plan(ctx.Item);
            if (plan.Count == 0)
            {
                return;
            }
            int n = Mask(PermCheck.Of(ctx), ctx.Item.Path, plan, result.Body);
            if (n > 0)
            {
                CoRows.Note(ctx.Item, "masked=" + n.ToString(CultureInfo.InvariantCulture));
            }
        }

        // 返回置空的字段名个数（去重）。load_many 的每个条目另带自己的 masked_fields，信封上是并集。
        internal static int Mask(PermContext p, string path, List<ColScope> plan, Dictionary<string, object> body)
        {
            if (p == null || p.Supervisor || p.Columns.Count == 0 || body == null || plan == null)
            {
                return 0;
            }
            HashSet<string> hit = new HashSet<string>(StringComparer.Ordinal);
            bool many = string.Equals(path, LoadMany.Path, StringComparison.Ordinal);
            for (int i = 0; i < plan.Count; i++)
            {
                Scope(p, plan[i], many, body, hit);
            }
            if (hit.Count > 0)
            {
                body[Key] = Sorted(hit);
            }
            return hit.Count;
        }

        static void Scope(PermContext p, ColScope scope, bool many, Dictionary<string, object> body, HashSet<string> hit)
        {
            Rule rule = RuleOf(p, scope.Keys);
            if (rule.Denied.Count == 0)
            {
                return;
            }
            if (scope.Section.Length > 0)
            {
                object part;
                if (body.TryGetValue(scope.Section, out part))
                {
                    Walk(part, rule, hit);
                }
            }
            else if (many)
            {
                Items(body, rule, hit);
            }
            else
            {
                Walk(body, rule, hit);
            }
        }

        // keys 为 null（类型没有登记对象）：全部对象的拒绝字段逐个比对，不展开金额组；否则这些对象的并集，有金额字段被拒绝时展开金额组。
        static Rule RuleOf(PermContext p, string[] keys)
        {
            Rule rule = new Rule();
            if (keys == null)
            {
                foreach (HashSet<string> set in p.Columns.Values)
                {
                    AddAll(rule, set);
                }
                return rule;
            }
            for (int i = 0; i < keys.Length; i++)
            {
                AddAll(rule, p.DeniedFields(keys[i]));
            }
            foreach (string name in rule.Denied)
            {
                rule.Group = rule.Group || PermColumnMap.IsMoney(name);
            }
            return rule;
        }

        static void AddAll(Rule rule, HashSet<string> set)
        {
            foreach (string fld in set)
            {
                string name = PermColumnMap.Normalize(fld);
                if (name.Length > 0)
                {
                    rule.Denied.Add(name);
                }
            }
        }

        static void Items(Dictionary<string, object> body, Rule rule, HashSet<string> hit)
        {
            object raw;
            IList items = body.TryGetValue("items", out raw) ? raw as IList : null;
            if (items == null)
            {
                Walk(body, rule, hit);
                return;
            }
            for (int i = 0; i < items.Count; i++)
            {
                IDictionary item = items[i] as IDictionary;
                if (item == null)
                {
                    continue;
                }
                HashSet<string> own = new HashSet<string>(StringComparer.Ordinal);
                Walk(item, rule, own);
                if (own.Count > 0)
                {
                    item[Key] = Sorted(own);
                    hit.UnionWith(own);
                }
            }
        }

        // 字典逐键判断（值是字典或数组时往下走，不整块置空）；数组逐个元素。
        static void Walk(object node, Rule rule, HashSet<string> hit)
        {
            IDictionary map = node as IDictionary;
            if (map != null)
            {
                List<object> keys = new List<object>();
                foreach (object key in map.Keys)
                {
                    keys.Add(key);
                }
                for (int i = 0; i < keys.Count; i++)
                {
                    object value = map[keys[i]];
                    string name = keys[i] as string;
                    if (Nested(value))
                    {
                        Walk(value, rule, hit);
                    }
                    else if (Denied(rule, name))
                    {
                        map[keys[i]] = null;
                        hit.Add(name);
                    }
                }
                return;
            }
            IEnumerable list = node as IEnumerable;
            if (list == null || node is string)
            {
                return;
            }
            foreach (object item in list)
            {
                if (Nested(item))
                {
                    Walk(item, rule, hit);
                }
            }
        }

        static bool Nested(object value)
        {
            return value is IEnumerable && !(value is string);
        }

        static bool Denied(Rule rule, string key)
        {
            if (key == null || key == "ok" || key == Key)
            {
                return false;
            }
            return rule.Denied.Contains(key) || (rule.Group && PermColumnMap.IsMoneyKey(key));
        }

        static List<string> Sorted(HashSet<string> names)
        {
            List<string> list = new List<string>(names);
            list.Sort(StringComparer.Ordinal);
            return list;
        }
    }
}
