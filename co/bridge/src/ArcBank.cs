using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 本单位开户银行新增、修改前的校验：在调用 Transact 之前按最终要发的整条记录查一遍，出错 400。
    // U8 自己的原文（实测）：缺币种「币种不可为空！」，缺所属银行「所属银行不可为空！」，
    // 所属银行（AA_Bank）设了企业账号定长时「银行账号要求定长（12位）！」。这里提前给出同样意思的中文。
    internal static class ArcBank
    {
        internal const string Name = "bank";
        const string BankSql = "SELECT bComdFixLen AS fixed, iComAccNoLen AS len FROM AA_Bank WHERE cBankCode=?";
        const string CurrencySql = "SELECT cexch_name FROM foreigncurrency WHERE cexch_name=?";

        // bag 是模板、当前行、调用方字段和缺省值合并后的整条记录（标签为 RsXml 写法，查找不分大小写）。
        public static void Check(object conn, ArcReq req, ArcBag bag)
        {
            if (req == null || req.Kind == null || req.Kind.Name != Name)
            {
                return;
            }
            Need(bag, "name", "开户银行名称 name");
            string account = Need(bag, "account", "银行账号 account");
            string bank = Need(bag, "cbankcode", "所属银行 cbankcode");
            string currency = Need(bag, "ccurrencyname", "币种 ccurrencyname");
            // 修改时只查调用方改动的部分：已有记录的旧值不因档案设置后来变了而挡住无关的修改（review 4b P3）。
            bool creating = req.Op == "create";
            if ((creating || req.Fields.Has("ccurrencyname")) && Rows.One(conn, CurrencySql, new object[] { currency }) == null)
            {
                throw ArcReq.Bad("币种 " + currency + " 不存在", "fields.ccurrencyname");
            }
            if (creating || req.Fields.Has("account") || req.Fields.Has("cbankcode"))
            {
                CheckAccount(conn, bank, account);
            }
        }

        // 企业账号定长：AA_Bank.bComdFixLen=1 时账号长度必须等于 iComAccNoLen。
        static void CheckAccount(object conn, string bank, string account)
        {
            Dictionary<string, object> row = Rows.One(conn, BankSql, new object[] { bank });
            if (row == null)
            {
                throw ArcReq.Bad("所属银行 " + bank + " 不存在（银行档案）", "fields.cbankcode");
            }
            if (ArcRead.Cell(row, "fixed") != "1")
            {
                return;
            }
            int len;
            string text = ArcRead.Cell(row, "len");
            if (text == null || !int.TryParse(text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out len) || len <= 0)
            {
                return;
            }
            if (account.Length != len)
            {
                throw ArcReq.Bad("银行账号要求定长（" + len.ToString(CultureInfo.InvariantCulture) + "位），所属银行 " + bank, "fields.account");
            }
        }

        static string Need(ArcBag bag, string tag, string label)
        {
            string value = bag.Get(tag);
            if (value == null || value.Trim().Length == 0)
            {
                throw ArcReq.Bad("缺少字段 " + label, FieldPath.Join("fields", tag));
            }
            return value;
        }
    }
}
