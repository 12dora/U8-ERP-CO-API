using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 记账请求里的一张凭证。Seq 是 dsign.isignseq，登录后查（GL_mpostcond1、GL_P_JZA 按它，不按类别字）。
    internal sealed class GlPostItem
    {
        public string Sign;
        public int No;
        public int Seq;
    }

    // gl/vouchers/post 的请求和登录后查到的总账选项。Poster 是操作员姓名（U8 写进 cbook）。
    internal sealed class GlPostReq
    {
        public int Year;
        public int Period;
        public List<GlPostItem> Items = new List<GlPostItem>();
        public bool Cash;
        public bool Master;
        public string Cond;
        public string Poster;
        HashSet<string> _targets;

        public GlKey Key(GlPostItem item)
        {
            GlKey key = new GlKey();
            key.Year = Year;
            key.Period = Period;
            key.Sign = item.Sign;
            key.No = item.No;
            return key;
        }

        // 本次要记账的凭证（按 isignseq、凭证号）。Seq 查到之后才能用。
        public bool Has(int year, int period, int seq, int no)
        {
            if (year != Year || period != Period)
            {
                return false;
            }
            if (_targets == null)
            {
                _targets = new HashSet<string>(StringComparer.Ordinal);
                foreach (GlPostItem item in Items)
                {
                    _targets.Add(Pair(item.Seq, item.No));
                }
            }
            return _targets.Contains(Pair(seq, no));
        }

        internal static string Pair(int seq, int no)
        {
            return seq.ToString(CultureInfo.InvariantCulture) + ":" + no.ToString(CultureInfo.InvariantCulture);
        }

        public string Summary()
        {
            StringBuilder sb = new StringBuilder();
            sb.Append(Year.ToString(CultureInfo.InvariantCulture)).Append("年").Append(Period.ToString(CultureInfo.InvariantCulture))
                .Append("期");
            for (int i = 0; i < Items.Count && i < 20; i++)
            {
                sb.Append(i == 0 ? " " : ",").Append(Items[i].Sign).Append("-").Append(Items[i].No.ToString(CultureInfo.InvariantCulture));
            }
            if (Items.Count > 20)
            {
                sb.Append(" 等 ").Append(Items.Count.ToString(CultureInfo.InvariantCulture)).Append(" 张");
            }
            return sb.ToString();
        }
    }

    // 登录前的字段校验（只抛 400）、写锁键、GL_P_JZA 的 @tcond。
    internal static class GlPostParse
    {
        public const int Max = 200;
        public const string Rule = "write:gl:post";
        // GL_P_JZA 的 @tcond、@ss 都是 nvarchar(4000)，出纳签字分支把 @tcond 拼进 @ss 两次，再加约 800 字的固定文本。
        public const int CondMax = 1500;
        static readonly string[] ItemNames = new string[] { "sign", "no" };

        // loginYear 为 0 时只校验形状（登录前），fiscal_year 省略时年度留 0。
        public static GlPostReq Parse(Dictionary<string, object> body, int loginYear)
        {
            GlPostReq req = new GlPostReq();
            req.Period = GlReq.IntIn(GlReq.Field(body, "period"), "period", 1, 12);
            object year = GlReq.Field(body, "fiscal_year");
            req.Year = year == null ? loginYear : GlReq.IntIn(year, "fiscal_year", 1900, 9999);
            object[] list = GlReq.Field(body, "vouchers") as object[];
            if (list == null || list.Length < 1 || list.Length > Max)
            {
                throw GlReq.Bad("vouchers 必须是 1 到 200 张凭证的数组", "vouchers");
            }
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i < list.Length; i++)
            {
                GlPostItem item = ItemAt(list[i], i);
                if (!seen.Add(item.Sign + "-" + item.No.ToString(CultureInfo.InvariantCulture)))
                {
                    throw GlReq.Bad("vouchers 里凭证 " + item.Sign + "-" + item.No.ToString(CultureInfo.InvariantCulture) + " 重复",
                        FieldPath.Item("vouchers", i));
                }
                req.Items.Add(item);
            }
            return req;
        }

        // 项内校验的 field 相对该项（如 sign），这里补上 vouchers.<下标>。
        static GlPostItem ItemAt(object raw, int i)
        {
            try
            {
                return Item(raw, i + 1);
            }
            catch (BridgeException ex)
            {
                throw GlReq.Under(ex, FieldPath.Item("vouchers", i));
            }
        }

        static GlPostItem Item(object raw, int index)
        {
            string at = "vouchers 第 " + index.ToString(CultureInfo.InvariantCulture) + " 项";
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw GlReq.Bad(at + "必须是对象");
            }
            foreach (string name in map.Keys)
            {
                if (Array.IndexOf(ItemNames, name) < 0)
                {
                    throw GlReq.Bad(at + "不能设置字段 " + name, name);
                }
            }
            GlPostItem item = new GlPostItem();
            item.Sign = GlReq.SignText(GlReq.Field(map, "sign"), at + " sign");
            item.No = GlReq.IntIn(GlReq.Field(map, "no"), at + " no", 1, 32767);
            return item;
        }

        // 写锁键（DocLocks）：记账一把全局键 "gl:post"（GL_mpostcond1、GL_Acc_Temp* 各年度共用，U8 回写 ibook 的 UPDATE
        // 也不分年度），再加每张凭证的 "gl:<年>:<期>:<类别>:<号>"，与同一张凭证的审核、签字等串行。
        // 登录前已校验过形状；这里不抛异常，取不到的项跳过。
        public static string[] LockKeys(Dictionary<string, object> body, string loginYear)
        {
            List<string> keys = new List<string>();
            keys.Add("gl:post");
            object fiscal = Requests.Field(body, "fiscal_year");
            string year = fiscal == null ? loginYear : Requests.KeyPart(fiscal);
            string period = Requests.KeyPart(Requests.Field(body, "period"));
            object[] list = Requests.Field(body, "vouchers") as object[];
            if (list == null)
            {
                return keys.ToArray();
            }
            for (int i = 0; i < list.Length; i++)
            {
                Dictionary<string, object> map = list[i] as Dictionary<string, object>;
                if (map == null)
                {
                    continue;
                }
                string key = "gl:" + year + ":" + period + ":" + Requests.KeyPart(Requests.Field(map, "sign"))
                    + ":" + Requests.KeyPart(Requests.Field(map, "no"));
                if (!keys.Contains(key))
                {
                    keys.Add(key);
                }
            }
            return keys.ToArray();
        }

        // 照 U8 界面 UCPost.PostCondStr：每个类别一段 "(isignseq=<s> and ino_id in (<n>,…))"，各段 " or " 相连；
        // 主管签字开启（@imaster=1）时 GL_P_JZA 用别名 A，列名写 A.isignseq / A.ino_id。只拼校验过的整数。
        public static string Cond(GlPostReq req)
        {
            SortedDictionary<int, List<int>> bySeq = new SortedDictionary<int, List<int>>();
            foreach (GlPostItem item in req.Items)
            {
                List<int> nos;
                if (!bySeq.TryGetValue(item.Seq, out nos))
                {
                    nos = new List<int>();
                    bySeq.Add(item.Seq, nos);
                }
                nos.Add(item.No);
            }
            string seqCol = req.Master ? "A.isignseq" : "isignseq";
            string noCol = req.Master ? "A.ino_id" : "ino_id";
            StringBuilder sb = new StringBuilder();
            foreach (KeyValuePair<int, List<int>> pair in bySeq)
            {
                pair.Value.Sort();
                if (sb.Length > 0)
                {
                    sb.Append(" or ");
                }
                sb.Append("(").Append(seqCol).Append("=").Append(pair.Key.ToString(CultureInfo.InvariantCulture))
                    .Append(" and ").Append(noCol).Append(" in (").Append(Join(pair.Value)).Append("))");
            }
            if (sb.Length > CondMax)
            {
                throw GlReq.Bad("一次记账的凭证过多（U8 的记账范围条件最长 " + CondMax.ToString(CultureInfo.InvariantCulture)
                    + " 字），请分批记账");
            }
            return sb.ToString();
        }

        static string Join(List<int> nos)
        {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < nos.Count; i++)
            {
                if (i > 0)
                {
                    sb.Append(",");
                }
                sb.Append(nos[i].ToString(CultureInfo.InvariantCulture));
            }
            return sb.ToString();
        }
    }
}
