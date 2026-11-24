using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 合并检验的一条合并规则（U8 枚举 QM.ProMergeCheckRule 的编码）：勾选后各来源的这些列必须相同。Cols 是 QmMergeSrc.PeekSql 的列别名。
    internal sealed class QmMergeRule
    {
        public string Code;
        public string Name;
        public string[] Cols;
    }

    // 请求的 merge_rules：U8 合并规则编码的数组，缺省 ["0"]（自由项，U8 合并检验参照的缺省勾选）。
    // 支持 0 自由项（报检行 CFREE1–10）、1 生产部门（报检单 CDEPCODE）、2 仓库、4 合同号、5 生产订单号、6 客户（报检单 CCUSCODE）、
    // 7 生产批号、8 项目大类 + 项目（以上除注明外都取报检单行）。3 需求跟踪（U8 按需求跟踪方式、订单号、行号比较，报检行上对应的列未核实）
    // 和其他编码 400。表头 MERGECHECKRULE 写编码升序、逗号分隔（多选时 U8 的分隔符未实测）。
    internal static class QmMergeRules
    {
        internal const string Field = "merge_rules";
        static readonly QmMergeRule[] All = new QmMergeRule[]
        {
            Make("0", "自由项", "CFREE1", "CFREE2", "CFREE3", "CFREE4", "CFREE5", "CFREE6", "CFREE7", "CFREE8", "CFREE9", "CFREE10"),
            Make("1", "生产部门", "RDEP"),
            Make("2", "仓库", "RWH"),
            Make("4", "合同号", "RCONTRACT"),
            Make("5", "生产订单号", "RMO"),
            Make("6", "客户", "RCUS"),
            Make("7", "生产批号", "RPROBATCH"),
            Make("8", "项目大类+项目", "RITEMCLASS", "RITEMCODE")
        };

        static QmMergeRule Make(string code, string name, params string[] cols)
        {
            QmMergeRule rule = new QmMergeRule();
            rule.Code = code;
            rule.Name = name;
            rule.Cols = cols;
            return rule;
        }

        // 缺省 ["0"]；给了必须是 1 到 9 个不重复的编码（整数或数字串）。
        internal static List<QmMergeRule> Parse(object raw)
        {
            List<QmMergeRule> rules = new List<QmMergeRule>();
            if (raw == null)
            {
                rules.Add(All[0]);
                return rules;
            }
            IList list = raw as IList;
            if (list == null || list.Count < 1 || list.Count > 9)
            {
                throw BridgeException.BadField(Field, "merge_rules 必须是 1 到 9 个 U8 合并规则编码");
            }
            for (int i = 0; i < list.Count; i++)
            {
                QmMergeRule rule = Find(Code(list[i]));
                if (rule == null)
                {
                    throw BridgeException.BadField(FieldPath.Item(Field, i), "不支持的合并规则 " + Values.Text(list[i]));
                }
                if (rules.Contains(rule))
                {
                    throw BridgeException.BadField(FieldPath.Item(Field, i), "合并规则重复");
                }
                rules.Add(rule);
            }
            rules.Sort(delegate(QmMergeRule a, QmMergeRule b) { return string.CompareOrdinal(a.Code, b.Code); });
            return rules;
        }

        static string Code(object value)
        {
            if (value is int || value is long)
            {
                return Convert.ToString(value, CultureInfo.InvariantCulture);
            }
            string text = value as string;
            return text == null ? "" : text.Trim();
        }

        static QmMergeRule Find(string code)
        {
            for (int i = 0; i < All.Length; i++)
            {
                if (All[i].Code == code)
                {
                    return All[i];
                }
            }
            return null;
        }

        // 表头 MERGECHECKRULE。
        internal static string Text(List<QmMergeRule> rules)
        {
            List<string> codes = new List<string>();
            for (int i = 0; i < rules.Count; i++)
            {
                codes.Add(rules[i].Code);
            }
            return string.Join(",", codes.ToArray());
        }

        // 第一条规则对不上时返回其名称，全对上返回空串。值去两端空白后按原样比较（NULL 与空串相同）。
        internal static string Mismatch(List<QmMergeRule> rules, Dictionary<string, object> first, Dictionary<string, object> other)
        {
            for (int i = 0; i < rules.Count; i++)
            {
                for (int k = 0; k < rules[i].Cols.Length; k++)
                {
                    string col = rules[i].Cols[k];
                    if (CoRows.Col(first, col).Trim() != CoRows.Col(other, col).Trim())
                    {
                        return rules[i].Name;
                    }
                }
            }
            return "";
        }
    }
}
