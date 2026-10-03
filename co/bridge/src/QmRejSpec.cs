using System;
using System.Collections.Generic;

namespace U8Co
{
    // 不良品处理单的两类单据：QM05 来料（参照来料检验单 QM03）、QM06 产品（参照产品检验单 QM04）。
    // 组件 UFQMCo.clsArrRejectCO / clsProRejectCO，VT 355 / 356，参照视图 QM_QARRCHECKB / QM_QPROCHECKB（U8 参照生单的同一视图）。
    // 功能 id 取自 UFMeta 的 AA_FormButtonAuths：来料不良品处理单卡片 QM_QM020103_Doc 的 Add / Erase / Sure / UnSure 是
    // QM02010303 / 04 / 05 / 07；产品不良品处理单 QM_QM020203_Doc 是 QM02020303 / 04 / 05 / 07。
    internal sealed class QmRejSpec
    {
        public string Kind;
        public string VouchType;
        public string ProgId;
        public int Vt;
        public string CheckKind;
        public string CheckType;
        public string CheckView;
        public bool Incoming;
        public string AddAuth;
        public string DeleteAuth;
        public string VerifyAuth;
        public string UnverifyAuth;

        static readonly QmRejSpec[] All = new QmRejSpec[]
        {
            Make("qm_incoming_reject", "QM05", "UFQMCo.clsArrRejectCO", 355, "QM020103"),
            Make("qm_product_reject", "QM06", "UFQMCo.clsProRejectCO", 356, "QM020203")
        };

        public static QmRejSpec Of(VoucherKind kind)
        {
            return kind == null ? null : Find(kind.Name);
        }

        public static QmRejSpec Find(string name)
        {
            for (int i = 0; i < All.Length; i++)
            {
                if (All[i].Kind == name)
                {
                    return All[i];
                }
            }
            return null;
        }

        public static bool Handles(VoucherKind kind)
        {
            return Of(kind) != null;
        }

        public static IEnumerable<QmRejSpec> List()
        {
            return All;
        }

        // 写规则键：write:<类型>:create|delete|verify|unverify（PermRegistry.QmWriteRules 登记）。
        public string RuleKey(string op)
        {
            return "write:" + Kind + ":" + op;
        }

        public VoucherKind KindOf()
        {
            VoucherKind kind = Kinds.Find(Kind);
            if (kind == null)
            {
                throw new BridgeException(500, "internal", "单据类型配置无效");
            }
            return kind;
        }

        public string Title
        {
            get { return Incoming ? "来料不良品处理单" : "产品不良品处理单"; }
        }

        // QM05 是来料（来源 QM03 来料检验单），QM06 是产品（来源 QM04 产品检验单）。
        static QmRejSpec Make(string kind, string vouchType, string progId, int vt, string auth)
        {
            QmRejSpec s = new QmRejSpec();
            s.Kind = kind;
            s.VouchType = vouchType;
            s.ProgId = progId;
            s.Vt = vt;
            s.Incoming = vouchType == "QM05";
            s.CheckKind = s.Incoming ? "qm_incoming_check" : "qm_product_check";
            s.CheckType = s.Incoming ? "QM03" : "QM04";
            s.CheckView = s.Incoming ? "QM_QARRCHECKB" : "QM_QPROCHECKB";
            s.AddAuth = auth + "03";
            s.DeleteAuth = auth + "04";
            s.VerifyAuth = auth + "05";
            s.UnverifyAuth = auth + "07";
            return s;
        }

        public static bool Same(string a, string b)
        {
            return string.Equals(a, b, StringComparison.OrdinalIgnoreCase);
        }
    }
}
