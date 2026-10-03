using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 校验过的质量单据修改请求（QmEditReq.Parse）。Head 的键是小写字段名（不含 items 和三个数量），值已去两端空白。
    internal sealed class QmEditAsk
    {
        public VoucherKind Kind;
        // 检验单（QM03 / QM04 / QM15）为真；其他报检单（QM11）为假。
        public bool Check;
        public readonly Dictionary<string, string> Head = new Dictionary<string, string>(StringComparer.Ordinal);
        // 合格、让步、不良数量：null 表示没送。
        public decimal? Reg;
        public decimal? Con;
        public decimal? Dis;
        // 检验项目覆盖：null 表示没送。
        public List<QmItemAsk> Items;

        public bool Split
        {
            get { return Reg.HasValue || Con.HasValue || Dis.HasValue; }
        }

        public bool Has(string low)
        {
            return Head.ContainsKey(low);
        }

        public string Get(string low)
        {
            string value;
            return Head.TryGetValue(low, out value) ? value ?? "" : "";
        }
    }

    // 质量单据修改（vouchers/update）的请求校验，登录前和处理函数里各一次，字段名不分大小写。只收 head，不收 lines。
    // 检验单（来料 QM03、产品 QM04、其他 QM15）：ddate、ccheckpersoncode、cchkconclusion、creasoncode、fregquantity、fconquantiy、
    // fdisquantity、cdefine1–16、chdefine11–16、items（按检验项目和指标覆盖已有行，不增删行）。检验数量 FQUANTITY、存货、仓库、
    // 检验方案、来源都不能改；三个数量之和必须等于单据上已有的检验数量（处理函数里核对）。
    // 其他报检单（QM11）：ddate、cinspectdepcode、cdefine1–16、chdefine11–16（表体数量、仓库不开放修改）。
    internal static class QmEditReq
    {
        public const string UpdatePath = "/u8co/v1/vouchers/update";
        const int DefineMax = 120;
        static readonly string[] CheckHead = new string[]
        {
            "ddate", "ccheckpersoncode", "cchkconclusion", "creasoncode", "fregquantity", "fconquantiy", "fdisquantity", "items",
            QmYield.CodeKey, QmYield.DateKey
        };
        static readonly string[] InspectHead = new string[] { "ddate", "cinspectdepcode" };
        static readonly string[] SplitKeys = new string[] { "fregquantity", "fconquantiy", "fdisquantity" };

        // 修改按钮的功能 id，已按 UFMeta 按钮权限核对（AA_FormButtonAuths，cButtonKey='Modify'）：修改与新增、保存同一个 id
        // （卡片 QM_QM020102_Doc、QM_QM020202_Doc、QM_QM020602_Doc、QM_QM020601_Doc 的 …03）。
        static readonly string[][] Auths = new string[][]
        {
            new string[] { "qm_incoming_check", "QM02010203" },
            new string[] { "qm_product_check", "QM02020203" },
            new string[] { QmOthSpec.CheckKind, "QM02060203" },
            new string[] { QmOthSpec.InspectKind, "QM02060103" }
        };

        public static string Auth(string kind)
        {
            for (int i = 0; i < Auths.Length; i++)
            {
                if (Auths[i][0] == kind)
                {
                    return Auths[i][1];
                }
            }
            return "";
        }

        // 可修改的四类：来料 / 产品检验单（QmSpec 里不是报检单的）、其他报检单、其他检验单。
        public static bool Handles(VoucherKind kind)
        {
            if (kind == null)
            {
                return false;
            }
            QmSpec spec = QmSpec.Of(kind);
            return (spec != null && !spec.Inspect) || QmOthSpec.Handles(kind);
        }

        public static bool IsCheck(VoucherKind kind)
        {
            QmSpec spec = QmSpec.Of(kind);
            return (spec != null && !spec.Inspect) || QmOthSpec.IsCheck(kind);
        }

        // QmReq.Check 的钩子：修改用 QM 登录，登录前校验。其他路由、其他类型返回 false。
        public static bool Check(WorkItem item, string path)
        {
            if (item == null || path != UpdatePath || !Handles(item.Type))
            {
                return false;
            }
            Parse(item.Type, item.Head, item.Lines);
            item.SubId = QmCo.LoginSub;
            return true;
        }

        public static QmEditAsk Parse(VoucherKind kind, Dictionary<string, object> head, object[] lines)
        {
            if (lines != null && lines.Length > 0)
            {
                throw BridgeException.BadField("lines", "质量单据修改只收表头 head（检验项目放在 head.items），不收 lines");
            }
            if (head == null || head.Count == 0)
            {
                throw BridgeException.BadField("head", "没有要修改的内容");
            }
            QmEditAsk ask = new QmEditAsk();
            ask.Kind = kind;
            ask.Check = IsCheck(kind);
            try
            {
                foreach (KeyValuePair<string, object> kv in head)
                {
                    Field(ask, kv.Key, kv.Value);
                }
                Validate(ask);
            }
            catch (BridgeException ex)
            {
                throw FieldPath.Under(ex, "head");
            }
            return ask;
        }

        static void Field(QmEditAsk ask, string key, object value)
        {
            string low = key == null ? "" : key.ToLowerInvariant();
            if (!HeadKey(ask.Check, low))
            {
                throw BridgeException.BadField(key, "不能设置字段 " + key).WithHint(FieldPath.WritableHint);
            }
            if (low == "items")
            {
                ask.Items = QmItemAsk.ParseList(value);
                return;
            }
            if (Array.IndexOf(SplitKeys, low) >= 0)
            {
                SetSplit(ask, low, QmReq.Qty(value, low, true));
                return;
            }
            bool define = low.StartsWith("cdefine", StringComparison.Ordinal) || low.StartsWith("chdefine", StringComparison.Ordinal);
            ask.Head[low] = QmReq.Text(value, key, define ? DefineMax : QmReq.ItemTextMax);
        }

        static void SetSplit(QmEditAsk ask, string low, decimal qty)
        {
            if (low == "fregquantity")
            {
                ask.Reg = qty;
            }
            else if (low == "fconquantiy")
            {
                ask.Con = qty;
            }
            else
            {
                ask.Dis = qty;
            }
        }

        // 送了就不能是空串：清空必输字段 U8 保存时才报错，而且检验员、结论、原因、报检部门清空没有业务意义。
        static void Validate(QmEditAsk ask)
        {
            if (ask.Has("ddate") && !IsDate(ask.Get("ddate")))
            {
                throw BridgeException.BadField("ddate", "单据日期必须是 yyyy-MM-dd");
            }
            QmYield.CheckDate(ask.Get(QmYield.DateKey), QmYield.DateKey);
            string[] need = new string[] { "ccheckpersoncode", "cchkconclusion", "creasoncode", "cinspectdepcode", QmYield.CodeKey };
            for (int i = 0; i < need.Length; i++)
            {
                if (ask.Has(need[i]) && ask.Get(need[i]).Length == 0)
                {
                    throw BridgeException.BadField(need[i], need[i] + " 不能为空");
                }
            }
        }

        internal static bool HeadKey(bool check, string low)
        {
            if (Array.IndexOf(check ? CheckHead : InspectHead, low) >= 0 || QmReq.Span(low, "cdefine", 1, 16))
            {
                return true;
            }
            return QmReq.Span(low, "chdefine", 11, 16);
        }

        internal static bool HeadKey(VoucherKind kind, string low)
        {
            return HeadKey(IsCheck(kind), low);
        }

        // 合格、让步、不良：没送的让步、不良取 0，没送的合格 = 检验数量 − 让步 − 不良；三者之和必须等于单据上已有的检验数量。
        internal static decimal[] Resolve(QmEditAsk ask, decimal qty)
        {
            decimal con = ask.Con.HasValue ? ask.Con.Value : 0m;
            decimal dis = ask.Dis.HasValue ? ask.Dis.Value : 0m;
            decimal reg = QmMath.Qualified(qty, ask.Reg.HasValue, ask.Reg.HasValue ? ask.Reg.Value : 0m, con, dis);
            return new decimal[] { reg, con, dis };
        }

        // CREASONCODE 是「让步接收原因」：通常只在有让步数量时填写。
        // 改了数量且让步数量大于 0 时必须有原因（请求送了或单据上已有）。onlyWithConcession（其他检验单）：让步数量为 0 时不收原因，
        // U8 审核时报「没有'让步接收数量'，'让步接收原因'不能填写！」，桥在调用前 400。con 是修改后的让步数量。
        internal static void RequireReason(QmEditAsk ask, bool split, decimal con, string existing, bool onlyWithConcession)
        {
            if (onlyWithConcession && con <= 0m && ask.Has("creasoncode"))
            {
                throw BridgeException.BadField("head.creasoncode", "没有让步接收数量时不能填写让步接收原因 creasoncode");
            }
            if (split && con > 0m && !ask.Has("creasoncode") && (existing ?? "").Trim().Length == 0)
            {
                throw BridgeException.BadField("head.creasoncode", "有让步接收数量时必须指定让步接收原因 creasoncode");
            }
        }

        // meta 用的候选字段名（其余已在 QmReq.MetaNames 里）。
        internal static string[] MetaNames()
        {
            return new string[] { "creasoncode" };
        }

        static bool IsDate(string text)
        {
            DateTime date;
            return DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out date);
        }
    }
}
