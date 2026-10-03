using System;
using System.Collections.Generic;

namespace U8Co
{
    // EAI 的 proc="edit" 是删掉再按报文重插。报文里有、调用方又不能填的列，修改时从原凭证原样带过去：
    // ctext1/ctext2（memo1/memo2）、coutsign（reserve1），按分录号抄 coutbillsign/coutid（bill_type/bill_id）和 cname（业务员）。
    // 抄不回去的（红字冲销关联 cblueoutno_id、自定义项）一律拒绝修改。GL_CodeRemark 只有键列，没有内容可丢。
    internal sealed class GlCarry
    {
        internal static readonly string[] Empty = new string[] { "", "", "" };

        // 自定义项都为空时 0，否则 1（列别名 defs）。红字冲销（GlReverseSrc）也用它拒绝带自定义项的原凭证。
        internal const string DefsCol = " CASE WHEN ISNULL(cDefine1,'')='' AND ISNULL(cDefine2,'')='' AND ISNULL(cDefine3,'')='' AND cDefine4 IS NULL"
            + " AND cDefine5 IS NULL AND cDefine6 IS NULL AND cDefine7 IS NULL AND ISNULL(cDefine8,'')=''"
            + " AND ISNULL(cDefine9,'')='' AND ISNULL(cDefine10,'')='' AND ISNULL(cDefine11,'')='' AND ISNULL(cDefine12,'')=''"
            + " AND ISNULL(cDefine13,'')='' AND ISNULL(cDefine14,'')='' AND cDefine15 IS NULL AND cDefine16 IS NULL"
            + " THEN 0 ELSE 1 END defs";

        const string CarrySql = "SELECT inid, ISNULL(ccode,'') ccode, ISNULL(coutsign,'') outsign, ISNULL(ctext1,'') t1, ISNULL(ctext2,'') t2,"
            + " ISNULL(coutbillsign,'') bt, ISNULL(coutid,'') bid, ISNULL(cname,'') op, ISNULL(cblueoutno_id,'') blue,"
            + DefsCol + " FROM GL_accvouch" + GlState.KeyWhere + " ORDER BY inid";

        public string OutSign = "";
        public string Memo1 = "";
        public string Memo2 = "";
        readonly Dictionary<int, string[]> _lines = new Dictionary<int, string[]>();
        readonly Dictionary<int, string> _accounts = new Dictionary<int, string>();

        public string[] At(int entry)
        {
            string[] old;
            return _lines.TryGetValue(entry, out old) ? old : Empty;
        }

        // 红字冲销（GlReverseSrc）逐行登记原凭证同一分录号的 [原始单据类型, 原始单据号, 业务员]。
        internal void Put(int entry, string account, string[] old)
        {
            _accounts[entry] = account;
            _lines[entry] = old;
        }

        // 按分录号原样带过去。带着原始单据或业务员的分录，新报文同一分录号必须还是同一科目，否则拒绝，不去猜对应关系。
        public void Match(GlDraft draft)
        {
            foreach (KeyValuePair<int, string[]> pair in _lines)
            {
                string[] old = pair.Value;
                if (old[0].Length == 0 && old[1].Length == 0 && old[2].Length == 0)
                {
                    continue;
                }
                int index = pair.Key - 1;
                if (index < 0 || index >= draft.Lines.Count
                    || !draft.Lines[index].Account.Equals(_accounts[pair.Key], StringComparison.OrdinalIgnoreCase))
                {
                    throw GlState.Refuse("修改不能改动分录的科目顺序（第 " + pair.Key.ToString(System.Globalization.CultureInfo.InvariantCulture)
                        + " 行原科目 " + _accounts[pair.Key] + " 带有原始单据或业务员）");
                }
            }
        }

        public static GlCarry Read(object conn, GlKey key)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, CarrySql, GlSql.KeyArgs(key), 10000);
            if (rows.Count == 0)
            {
                throw new BridgeException(404, "not_found", "凭证不存在：" + key.Text());
            }
            GlCarry carry = new GlCarry();
            carry.OutSign = GlSql.Col(rows[0], "outsign");
            carry.Memo1 = GlSql.Col(rows[0], "t1");
            carry.Memo2 = GlSql.Col(rows[0], "t2");
            foreach (Dictionary<string, object> row in rows)
            {
                if (GlSql.Col(row, "blue").Length > 0)
                {
                    throw GlState.Refuse("凭证是红字冲销凭证，不能修改");
                }
                if (GlSql.Int(row, "defs") != 0)
                {
                    throw GlState.Refuse("凭证带自定义项，修改会丢失，请在 U8 客户端修改");
                }
                carry._accounts[GlSql.Int(row, "inid")] = GlSql.Col(row, "ccode");
                carry._lines[GlSql.Int(row, "inid")] = new string[]
                {
                    GlSql.Col(row, "bt"), GlSql.Col(row, "bid"), GlSql.Col(row, "op")
                };
            }
            return carry;
        }
    }
}
