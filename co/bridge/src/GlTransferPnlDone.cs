using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 期间损益的「本期已结转」判断。收入、费用两张凭证分两次导入（U8PzInsert 每次自己提交，不能合成一个事务），
    // 第二张失败时第一张已保存。已有的未作废期间损益凭证按其中损益类科目的性质认出是收入还是费用：
    // 已有的那张审核、记账后（取数只取已记账，它已把那一类结平），重新调用只算出缺的那张，照常生成，响应里 existing 列出已有的凭证；
    // 算出的凭证里有已存在的那一类、或认不出已有凭证是哪一类时照旧 409；什么都不缺时 409「该月已经做过期间损益结转」。
    internal static class GlTransferPnlDone
    {
        const string Sql = "SELECT csign + N'-' + CONVERT(varchar(12), ino_id) k, csign, CONVERT(varchar(12), ino_id) ino_id, ccode"
            + " FROM GL_accvouch WHERE iyear=? AND iperiod=? AND coutsign=N'" + GlTransferReq.PnlSign + "' AND ISNULL(iflag,0)<>1"
            + " ORDER BY csign, ino_id, inid";
        const int MaxRows = 20000;
        // 认不出是收入还是费用（没有损益类科目，或两类都有）。
        internal const string Unknown = "";

        // 已有的期间损益凭证（按凭证）：{类别-凭证号, 类别, 凭证号, 收入 / 费用 / 空串}。
        public static List<string[]> Existing(object conn, GlTransferAsk ask, GlTransferCodes codes)
        {
            List<string[]> list = new List<string[]>();
            foreach (Dictionary<string, object> row in Rows.Query(conn, Sql, new object[] { ask.Year, ask.Period }, MaxRows))
            {
                string key = GlSql.Col(row, "k");
                string[] last = list.Count > 0 ? list[list.Count - 1] : null;
                if (last == null || last[0] != key)
                {
                    last = new string[] { key, GlSql.Col(row, "csign"), GlSql.Col(row, "ino_id"), null };
                    list.Add(last);
                }
                last[3] = Merge(last[3], PackOf(codes, GlSql.Col(row, "ccode")));
            }
            foreach (string[] one in list)
            {
                one[3] = one[3] ?? Unknown;
            }
            return list;
        }

        // 一行分录的类别：损益类科目按性质（借方性质为费用），其他科目（本年利润）不表态（null）。
        internal static string PackOf(GlTransferCodes codes, string code)
        {
            Dictionary<string, object> row = codes.Find(code);
            if (row == null || GlSql.Col(row, "cclass") != ReportsMgmtPnlSql.PlClass)
            {
                return null;
            }
            return codes.DebitNature(code) ? GlTransferPnl.Expense : GlTransferPnl.Income;
        }

        static string Merge(string have, string pack)
        {
            if (pack == null)
            {
                return have;
            }
            if (have == null)
            {
                return pack;
            }
            return have == pack ? have : Unknown;
        }

        // 用已有凭证过滤算出的计划：有冲突 409，否则把已有凭证记进 plan.Existing。
        public static void Filter(GlTransferPlan plan, List<string[]> existing)
        {
            if (existing.Count == 0)
            {
                return;
            }
            List<string> keys = new List<string>();
            foreach (string[] one in existing)
            {
                keys.Add(one[0]);
                foreach (GlTransferVoucher v in plan.Vouchers)
                {
                    if (one[3] == Unknown || one[3] == v.Pack)
                    {
                        throw GlState.Refuse("该月已经做过期间损益结转（" + one[0] + "），如需重做请先作废并删除该凭证");
                    }
                }
            }
            if (plan.Vouchers.Count == 0)
            {
                throw GlState.Refuse("该月已经做过期间损益结转（" + string.Join("、", keys.ToArray()) + "），没有缺的凭证需要补生成");
            }
            foreach (string[] one in existing)
            {
                Dictionary<string, object> item = new Dictionary<string, object>();
                item["sign"] = one[1];
                int no;
                item["no"] = int.TryParse(one[2], NumberStyles.Integer, CultureInfo.InvariantCulture, out no) ? no : 0;
                item["pack"] = one[3];
                plan.Existing.Add(item);
            }
        }
    }
}
