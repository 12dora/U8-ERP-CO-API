using System;
using System.Collections.Generic;

namespace U8Co
{
    // 其他报检单（QM11，VT 361）、其他检验单（QM15，VT 365）的写入：无来源，没有审批流。
    // 组件是基于 VO 的接口（同不良品处理单，QmRejCo）：UFQMCo.clsOtherInspectVoucherCO / clsOtherCheckVoucherCO（能创建；
    // 没有 Init(login, conn, bOutTrans)，也没有 VoucherOperate）。类型库里两个组件都没有 Init；其他报检单组件没有
    // bOutAuth / bOutTrans，只设 LoadTemp。空白 DOM 取视图 QM_QOthInspectB/T、QM_QOthCheckB/T（where 1=2）。
    // 功能 id（UA_Auth 核对）：其他报检单新增 / 删除 / 审核 / 弃审 QM02060103 / 04 / 05 / 07，其他检验单 QM02060203 / 04 / 05 / 07。
    internal sealed class QmOthSpec
    {
        public const string InspectKind = "qm_other_inspect";
        public const string CheckKind = "qm_other_check";
        public const string CheckType = "OTH";

        // 其他检验单生单的写法（未经实测，联调时在这里切换）：true = AddNewVoucher 后 AddVoucherByRef（按其他报检单行的记录集带入，
        // 同不良品处理单参照检验单），false = 不调 AddVoucherByRef，表头表体全由桥填（同来料检验单的字段集）后 InitByXml。
        // 两种写法之后桥都按同一字段集改写表头、检验项目，所以切换只影响 U8 是否先带入。
        internal static readonly bool CheckByRef = true;

        public string Kind;
        public string VouchType;
        public string ProgId;
        public string View;
        public int Vt;
        public bool Inspect;
        public string Title;
        public string AddAuth;
        public string DeleteAuth;
        public string VerifyAuth;
        public string UnverifyAuth;

        static readonly QmOthSpec[] All = new QmOthSpec[]
        {
            Make(InspectKind, "QM11", "UFQMCo.clsOtherInspectVoucherCO", "QM_QOthInspect", 361, "QM020601"),
            Make(CheckKind, "QM15", "UFQMCo.clsOtherCheckVoucherCO", "QM_QOthCheck", 365, "QM020602")
        };

        // 其他检验单生单请求的校验同来料检验单（QmReq.Parse 只看 Inspect），检验方案缺省等也按 QM15 取：
        // 这个 QmSpec 不登记进 QmSpec.List，QmGen / QmDelete / PermRegistryQm 都看不到它。
        internal static readonly QmSpec CheckAsk = AskSpec();

        public static QmOthSpec Of(VoucherKind kind)
        {
            return kind == null ? null : Find(kind.Name);
        }

        public static QmOthSpec Find(string name)
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

        public static bool IsInspect(VoucherKind kind)
        {
            QmOthSpec spec = Of(kind);
            return spec != null && spec.Inspect;
        }

        public static bool IsCheck(VoucherKind kind)
        {
            QmOthSpec spec = Of(kind);
            return spec != null && !spec.Inspect;
        }

        public static IEnumerable<QmOthSpec> List()
        {
            return All;
        }

        // 写规则键：write:<类型>:create|delete|verify|unverify（PermRegistry.QmWriteRules 登记）。
        public string RuleKey(string op)
        {
            return "write:" + Kind + ":" + op;
        }

        public string HeadView
        {
            get { return View + "B"; }
        }

        public string BodyView
        {
            get { return View + "T"; }
        }

        public string CodeColumn
        {
            get { return Inspect ? "CINSPECTCODE" : "CCHECKCODE"; }
        }

        public string HeadTable
        {
            get { return Inspect ? "QMINSPECTVOUCHER" : "QMCHECKVOUCHER"; }
        }

        public string BodyTable
        {
            get { return Inspect ? "QMINSPECTVOUCHERS" : "QMCHECKVOUCHERS"; }
        }

        // 其他检验单组件有 bOutAuth / bOutTrans（设 True / False，同不良品处理单）；其他报检单组件没有。
        public QmRejCo Open(WorkContext ctx)
        {
            return QmRejCo.Open(ctx, ProgId, Vt, false, !Inspect);
        }

        static QmOthSpec Make(string kind, string vouchType, string progId, string view, int vt, string auth)
        {
            QmOthSpec s = new QmOthSpec();
            s.Kind = kind;
            s.VouchType = vouchType;
            s.ProgId = progId;
            s.View = view;
            s.Vt = vt;
            s.Inspect = vouchType == "QM11";
            s.Title = s.Inspect ? "其他报检单" : "其他检验单";
            s.AddAuth = auth + "03";
            s.DeleteAuth = auth + "04";
            s.VerifyAuth = auth + "05";
            s.UnverifyAuth = auth + "07";
            return s;
        }

        static QmSpec AskSpec()
        {
            QmOthSpec check = Find(CheckKind);
            QmSpec s = new QmSpec();
            s.Kind = check.Kind;
            s.VouchType = check.VouchType;
            s.ProgId = check.ProgId;
            s.HeadView = check.HeadView;
            s.BodyView = check.BodyView;
            s.Vt = check.Vt;
            s.CheckType = CheckType;
            s.SourceText = "";
            s.Inspect = false;
            s.Incoming = false;
            s.AddAuth = check.AddAuth;
            s.DeleteAuth = check.DeleteAuth;
            s.UnverifyAuth = check.UnverifyAuth;
            return s;
        }

        public static bool Same(string a, string b)
        {
            return string.Equals(a, b, StringComparison.OrdinalIgnoreCase);
        }
    }
}
