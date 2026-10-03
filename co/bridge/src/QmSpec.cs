using System;
using System.Collections.Generic;

namespace U8Co
{
    // 质量单据新增 / 删除 / 审核的四类单据：卡片、UFQMCo 组件、DOM 视图、VT、检验类型、来源、功能权限 id。
    // 功能 id 取自 UFMeta 的 AA_FormButtonAuths：来料报检单卡片 QM_QM020101_Doc 的 Add / Erase / Sure / UnSure 是
    // QM02010103 / 04 / 05 / 07；产品报检单 QM_QM020201_Doc 是 QM02020103 / 04 / 05 / 07；来料检验单 QM_QM020102_Doc 的
    // Add / Erase 是 QM02010203 / 04；产品检验单 QM_QM020202_Doc 是 QM02020203 / 04。
    internal sealed class QmSpec
    {
        public string Kind;
        public string VouchType;
        public string ProgId;
        public string HeadView;
        public string BodyView;
        public int Vt;
        public string CheckType;
        public string SourceText;
        // 报检单（QM01 / QM02）为真；检验单（QM03 / QM04）为假。
        public bool Inspect;
        // 来料（QM01 / QM03）为真；产品（QM02 / QM04）为假。
        public bool Incoming;
        public string AddAuth;
        public string DeleteAuth;
        public string UnverifyAuth;

        static readonly QmSpec[] All = Build();

        public static QmSpec Of(VoucherKind kind)
        {
            return kind == null ? null : Find(kind.Name);
        }

        public static QmSpec Find(string name)
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

        public static IEnumerable<QmSpec> List()
        {
            return All;
        }

        // 写规则键：write:<类型>:create|delete（PermRegistry.QmWriteRules 登记）。
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

        static QmSpec[] Build()
        {
            return new QmSpec[]
            {
                Make("qm_incoming_inspect", "QM01", "UFQMCo.clsArrInspectCO", "QM_QARRINSPECT", 351, "QM020101"),
                Make("qm_product_inspect", "QM02", "UFQMCo.clsProInspectCO", "QM_QPROINSPECT", 352, "QM020201"),
                Make("qm_incoming_check", "QM03", "UFQMCo.clsArrCheckCO", "QM_QARRCHECK", 353, "QM020102"),
                Make("qm_product_check", "QM04", "UFQMCo.clsProCheckCO", "QM_QPROCHECK", 354, "QM020202")
            };
        }

        // QM01 / QM02 是报检单，QM01 / QM03 是来料。
        static QmSpec Make(string kind, string vouchType, string progId, string view, int vt, string auth)
        {
            bool inspect = vouchType == "QM01" || vouchType == "QM02";
            bool incoming = vouchType == "QM01" || vouchType == "QM03";
            QmSpec s = new QmSpec();
            s.Kind = kind;
            s.VouchType = vouchType;
            s.ProgId = progId;
            s.HeadView = view + "B";
            s.BodyView = view + "T";
            s.Vt = vt;
            s.Inspect = inspect;
            s.Incoming = incoming;
            s.CheckType = incoming ? "ARR" : "PRO";
            s.SourceText = incoming ? "到货单" : "生产订单";
            s.AddAuth = auth + "03";
            s.DeleteAuth = auth + "04";
            s.UnverifyAuth = inspect ? auth + "07" : "";
            return s;
        }

        // 报检单的来源类型（arrival / production_order），检验单的来源是对应的报检单类型。
        public string SourceKind()
        {
            if (Inspect)
            {
                return Incoming ? "arrival" : "production_order";
            }
            return Incoming ? "qm_incoming_inspect" : "qm_product_inspect";
        }

        public static bool Same(string a, string b)
        {
            return string.Equals(a, b, StringComparison.OrdinalIgnoreCase);
        }
    }
}
