using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 调用方指定的现金流量项目（arap/voucher、arap/process/voucher 的 cash_items = {科目编码: 现金流量项目编码}）。
    // Items 的键不区分大小写；Used 记下本次凭证里真正用到的科目。
    internal sealed class CashItemMap
    {
        public Dictionary<string, string> Items = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        public HashSet<string> Used = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        // 退款单制单按分录所在列取方向（借方列有数即借方，含红字）。false 是原规则（借方金额大于 0 才算借方），处理制单用。
        public bool ByColumn;
    }

    // 应收应付制单的现金流量项目（arap/voucher 的 ArapVoucherPlan、arap/process/voucher 的票据处理和坏账收回共用）：
    // 总账选项 bXJLL 开着、凭证里既有现金流量科目又有别的科目时，别的科目（现金流量行）每行挂一个项目，金额、方向同该行；
    // 全是现金流量科目不挂。项目按「现金流量项目数据来源」GL_CashItemDataSource 推（Derive）；推不出时 U8 客户端由用户手选，
    // 这里对应 cash_items：行的科目在表里就用给的项目，不再推。
    internal static class ArapCashItems
    {
        public const string Field = "cash_items";
        public const int Max = 20;
        public const int AccountMax = 40;
        public const int ItemMax = 20;
        const string SourceSql = "select cItemCode as item, cDataSource as src, convert(varchar(4), isnull(bDir,0)) as dir from GL_CashItemDataSource "
            + "where iyear=? and isnull(cDataSource,N'')<>N'' and (dStartDate is null or dStartDate<=cast(convert(date, ?, 23) as datetime)) "
            + "and (dEndDate is null or dEndDate>=cast(convert(date, ?, 23) as datetime))";
        // 同 GlCheck.Refs：fitemss98 只放项目（分类在 fitemss98class），查得到即是末级项目。
        const string ItemSql = "SELECT TOP 1 'x' x FROM fitemss98 WHERE citemcode=? AND ISNULL(bclose,0)=0";

        // 登录前校验（400）：不给或 JSON null 是空表；必须是对象，最多 Max 项，键是不含空白、不超过 40 位的科目编码，
        // 值是不含空白、不超过 20 位的项目编码（首尾空格去掉）。
        public static CashItemMap Parse(object value)
        {
            CashItemMap map = new CashItemMap();
            if (value == null)
            {
                return map;
            }
            Dictionary<string, object> body = value as Dictionary<string, object>;
            if (body == null)
            {
                throw Bad("cash_items 必须是 {科目编码: 现金流量项目编码} 对象", Field);
            }
            if (body.Count > Max)
            {
                throw Bad("cash_items 最多 " + Max.ToString(CultureInfo.InvariantCulture) + " 个科目", Field);
            }
            foreach (KeyValuePair<string, object> pair in body)
            {
                string at = FieldPath.Join(Field, pair.Key);
                string account = Code(pair.Key, AccountMax);
                if (account == null)
                {
                    throw Bad("cash_items 的键必须是不超过 40 位、不含空白的科目编码", at);
                }
                string item = Code(pair.Value as string, ItemMax);
                if (item == null)
                {
                    throw Bad("cash_items 的值必须是不超过 20 位、不含空白的现金流量项目编码", at);
                }
                if (map.Items.ContainsKey(account))
                {
                    throw Bad("cash_items 有重复的科目 " + account, at);
                }
                map.Items[account] = item;
            }
            return map;
        }

        static string Code(string text, int max)
        {
            string code = text == null ? "" : text.Trim();
            if (code.Length == 0 || code.Length > max)
            {
                return null;
            }
            foreach (char c in code)
            {
                if (char.IsControl(c) || char.IsWhiteSpace(c))
                {
                    return null;
                }
            }
            return code;
        }

        // lines：全部分录；cash[i]：第 i 行的科目是不是现金流量科目（code.bCashItem）。挂完后核对 cash_items（Finish）。
        // 现金流量行的金额、列都同该分录（含负数）；方向规则见 CashItemMap.ByColumn。
        public static void Flows(object conn, List<GlLine> lines, List<bool> cash, int year, string date, CashItemMap given)
        {
            if (Needed(cash) && GlState.Option(conn, "bXJLL", true))
            {
                List<Dictionary<string, object>> sources = Rows.Query(conn, SourceSql, new object[] { year, date, date }, 5001);
                for (int i = 0; i < lines.Count; i++)
                {
                    if (cash[i])
                    {
                        continue;
                    }
                    GlLine line = lines[i];
                    GlFlow flow = new GlFlow();
                    flow.Item = Pick(sources, line, given);
                    flow.Debit = line.Debit;
                    flow.Credit = line.Credit;
                    line.Flows.Add(flow);
                }
            }
            Finish(conn, given);
        }

        static bool Needed(List<bool> cash)
        {
            bool any = false;
            bool all = true;
            foreach (bool one in cash)
            {
                any = any || one;
                all = all && one;
            }
            return any && !all;
        }

        // 一行现金流量行的项目：科目在 cash_items 里用给的（记入 Used），否则按数据来源推。
        internal static string Pick(List<Dictionary<string, object>> sources, GlLine line, CashItemMap given)
        {
            string item;
            if (given != null && given.Items.TryGetValue(line.Account, out item))
            {
                given.Used.Add(line.Account);
                return item;
            }
            return Derive(sources, line, given != null && given.ByColumn);
        }

        // 数据来源的方向：1 借方、0 贷方（byColumn 见 CashItemMap.ByColumn）。
        internal static string Dir(GlLine line, bool byColumn)
        {
            return (byColumn ? line.Debit != 0 : line.Debit > 0) ? "1" : "0";
        }

        // 数据来源是科目编码前缀，bDir 1 对借方行、0 对贷方行（借方金额大于 0 才算借方，贷方负数行按贷方取，如 660399 贷 -10.00 → 07；
        // 收款的应收贷方取 1122 / 0，付款的应付借方取 2202 / 1）；取最长的前缀，同样长给出不同项目或没有 → 409。
        internal static string Derive(List<Dictionary<string, object>> sources, GlLine line)
        {
            return Derive(sources, line, false);
        }

        internal static string Derive(List<Dictionary<string, object>> sources, GlLine line, bool byColumn)
        {
            string dir = Dir(line, byColumn);
            string best = null;
            int len = -1;
            bool clash = false;
            foreach (Dictionary<string, object> src in sources)
            {
                string prefix = CoRows.Col(src, "src");
                if (CoRows.Col(src, "dir") != dir || !line.Account.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                string item = CoRows.Col(src, "item");
                if (prefix.Length > len)
                {
                    best = item;
                    len = prefix.Length;
                    clash = false;
                }
                else if (prefix.Length == len && item != best)
                {
                    clash = true;
                }
            }
            if (best == null || best.Length == 0 || clash)
            {
                throw ArapVoucherDoc.Refuse("科目 " + line.Account + " 的现金流量项目无法按数据来源唯一确定，请用 cash_items 指定，或在 U8 客户端制单");
            }
            return best;
        }

        // 核对 cash_items（400）：每个科目都要在本次凭证里有现金流量行（不挂项目的凭证、处理类型一律没有）；
        // 用到的项目要存在且未关闭。先查科目（不连库），再查项目。
        public static void Finish(object conn, CashItemMap given)
        {
            if (given == null)
            {
                return;
            }
            foreach (string account in given.Items.Keys)
            {
                if (!given.Used.Contains(account))
                {
                    throw Bad("科目 " + account + " 在本次凭证里没有现金流量行", Field);
                }
            }
            foreach (KeyValuePair<string, string> pair in given.Items)
            {
                if (Rows.Scalar(conn, ItemSql, new object[] { pair.Value }) == null)
                {
                    throw Bad("现金流量项目 " + pair.Value + " 不存在或已关闭", FieldPath.Join(Field, pair.Key));
                }
            }
        }

        static BridgeException Bad(string message, string field)
        {
            return new BridgeException(400, "bad_request", message, field);
        }
    }
}
