using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 存货核算制单支持的单据类型（只收生产数据回测过、规则可复现的类型）：
    // 存货方向（入库借存货、出库贷存货）、对方科目取哪一列、来源单据表（表头主键、单号、补表头 cPZID）。
    // 对方：opp = 「对方科目」IA_OppHead.cOppHead；est = IA_OppHead.cEstHead（暂估）；
    // estFlag = 采购入库单：bFlag=1（暂估记账）取 cEstHead，否则 cOppHead；out = 「存货科目」IA_HeadSet.cOutHead（发出商品）。
    internal sealed class IaType
    {
        public string Code;
        public string Name;
        public bool In;
        public string Opp;
        // 来源单据表的后缀：01 / 08 / 09 / 10 / 11 / 32 是 RdRecordNN，just 是出入库调整单 JustInVouch。
        public string Table;
        // U8 制单后在来源单据表头写 cPZID（采购入库、其他入库 / 出库、产成品入库、材料出库）。
        public bool MarkHead;
        // 销售出库单（发出商品）另回写发出商品明细 IA_EnSubsidiary。
        public bool En;

        public static readonly IaType[] All = new IaType[]
        {
            Of("01", "采购入库单", true, "estFlag", "01", true),
            Of("08", "其他入库单", true, "opp", "08", true),
            Of("09", "其他出库单", false, "opp", "09", true),
            Of("10", "产成品入库单", true, "opp", "10", true),
            Of("11", "材料出库单", false, "opp", "11", true),
            Of("20", "入库调整单", true, "opp", "just", false),
            Of("21", "出库调整单", false, "opp", "just", false),
            Of("24", "红字回冲单", true, "est", "01", false),
            Of("30", "蓝字回冲单", true, "opp", "01", false),
            Of("3201", "销售出库单", false, "out", "32", false)
        };

        // 调拨、形态转换、组装、拆卸（cBusType 前两个字，同 U8 制单取数按 Left(cBusType, 2) 排除）：U8 的分录是成对单据的存货科目
        // 对冲或另有科目类型，没有普通的对方科目，桥不拼。
        public static readonly string[] PairBusiness = new string[] { "调拨", "转换", "组装", "拆卸" };

        // 回冲单（24 / 30）只收来源是采购入库单的（cSRcVoutype 空或 01）；VMI 等其他来源桥不拼。
        public bool SourceOk(string src)
        {
            return (Code != "24" && Code != "30") || src.Length == 0 || src == "01";
        }

        static IaType Of(string code, string name, bool isIn, string opp, string table, bool markHead)
        {
            IaType t = new IaType();
            t.Code = code;
            t.Name = name;
            t.In = isIn;
            t.Opp = opp;
            t.Table = table;
            t.MarkHead = markHead;
            t.En = code == "3201";
            return t;
        }

        public static IaType Find(string code)
        {
            foreach (IaType t in All)
            {
                if (t.Code == code)
                {
                    return t;
                }
            }
            return null;
        }

        public static string Codes()
        {
            List<string> codes = new List<string>();
            foreach (IaType t in All)
            {
                codes.Add(t.Code);
            }
            return string.Join("、", codes.ToArray());
        }
    }

    // 一行存货明细账（IA_Subsidiary）连同来源单据表头上的补充值；InHead / OppHead / Digest 在计划里填。
    internal sealed class IaRow
    {
        public string Aid = "";
        // IA_Subsidiary.ID（出入库单表体行 AutoID）：发出商品明细 IA_EnSubsidiary.InID 对它。
        public string Sid = "";
        public IaType Type;
        public int BillId;
        public string Code = "";
        public string VDate = "";
        public int Year;
        public int Month;
        public string BFlag = "";
        public string Business = "";
        public string Pz = "";
        public decimal Amount;
        public decimal Qty;
        public bool NoCost;
        public bool Dif;
        public string Inv = "";
        public string InvClass = "";
        public string Wh = "";
        public string Rd = "";
        public string Dept = "";
        public string Person = "";
        public string Vendor = "";
        public string Customer = "";
        public string ItemClass = "";
        public string Item = "";
        public string Memo = "";
        // 来源单据类型（IA_Subsidiary.cSRcVoutype，回冲单用）。
        public string Src = "";
        // 发出商品明细里对应的未制单行数（只有 3201）。
        public int En;
        public string InHead = "";
        public string OppHead = "";
        public string Digest = "";
    }

    // 一张来源单据（同一类型、同一表头主键）在存货明细账里的全部行。Skip 非空表示按月份选时跳过（原因）。
    internal sealed class IaBill
    {
        public IaType Type;
        public int Id;
        public string Code = "";
        public List<IaRow> Rows = new List<IaRow>();
        public string Skip = "";

        public string Label()
        {
            return Type.Name + " " + Code + "（" + Type.Code + "，id " + IaVoucherReq.Int(Id) + "）";
        }
    }

    // 一行分录的来源：合并进来的明细行中最大的单据类型、单号、单据日期（同 U8 的 Max），科目受控标志。
    internal sealed class IaLineSrc
    {
        public string BillType = "";
        public string BillCode = "";
        public string BillDate = "";
        public string Control = "";
        public bool Editable = true;
        // 存货方、发出商品方的分录不可改科目（U8 的 bcodeedit=0），对方等其他分录 1。
        public bool CodeEditable = true;
    }

    // 一张凭证的计划。
    internal sealed class IaPlan
    {
        public List<IaBill> Bills = new List<IaBill>();
        public GlKey Key = new GlKey();
        public int Seq;
        public string Date = "";
        public string PzId = "";
        public GlDraft Draft = new GlDraft();
        // 分录号 - 1 → 来源。
        public List<IaLineSrc> Src = new List<IaLineSrc>();
        // 分录号 → 业务员姓名（科目核算个人、客户或供应商时，同 U8 的 cname）。
        public Dictionary<int, string> Operators = new Dictionary<int, string>();
        public string MakingSystem = "";
        public string Rejected = "";
        public int MaxBefore;
        public bool Clash;

        public int RowCount()
        {
            int n = 0;
            foreach (IaBill bill in Bills)
            {
                n += bill.Rows.Count;
            }
            return n;
        }

        public int EnCount()
        {
            int n = 0;
            foreach (IaBill bill in Bills)
            {
                foreach (IaRow row in bill.Rows)
                {
                    n += row.En;
                }
            }
            return n;
        }

        public string PzNum()
        {
            string no = Key.No.ToString(CultureInfo.InvariantCulture);
            return Key.Sign + "-" + (no.Length < 4 ? new string('0', 4 - no.Length) : "") + no;
        }
    }

    // 一次请求：全部凭证计划（逐张时每张单据一份，合并时一份）、按月份选时跳过的单据、留到下次的单据（Pending）。
    internal sealed class IaJob
    {
        public IaVoucherAsk Ask;
        public int Year;
        public string Date = "";
        public string Sign = "";
        public List<IaBill> Bills = new List<IaBill>();
        public List<IaPlan> Plans = new List<IaPlan>();
        // 已保存、回写完的凭证数（第 Done 张起失败时，前面的保留）。
        public int Done;
        // 按月份逐张制单时超过一次上限、留到下次的单据。
        public List<IaBill> Later = new List<IaBill>();

        // 还没有制单的单据：第 Done 张起的计划里的单据，加上留到下次的。
        public List<object> Pending()
        {
            List<object> list = new List<object>();
            for (int i = Done; i < Plans.Count; i++)
            {
                foreach (IaBill bill in Plans[i].Bills)
                {
                    list.Add(Ref(bill));
                }
            }
            list.AddRange(Deferred());
            return list;
        }

        // 留到下次的单据（按月份逐张制单超过一次上限的）。
        public List<object> Deferred()
        {
            List<object> list = new List<object>();
            foreach (IaBill bill in Later)
            {
                list.Add(Ref(bill));
            }
            return list;
        }

        static Dictionary<string, object> Ref(IaBill bill)
        {
            Dictionary<string, object> one = new Dictionary<string, object>();
            one["type"] = bill.Type.Code;
            one["id"] = bill.Id;
            return one;
        }

        public List<object> Skipped()
        {
            List<object> list = new List<object>();
            foreach (IaBill bill in Bills)
            {
                if (bill.Skip.Length == 0)
                {
                    continue;
                }
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["type"] = bill.Type.Code;
                one["id"] = bill.Id;
                one["code"] = bill.Code;
                one["reason"] = bill.Skip;
                list.Add(one);
            }
            return list;
        }
    }

    // 合并分录时的比较：摘要取最大（同 U8 Max(cdigest)，按中文排序规则近似 SQL Server Chinese_PRC_CI_AS），编码类取不区分大小写的序数最大。
    internal static class IaText
    {
        static readonly StringComparer Zh = StringComparer.Create(new CultureInfo("zh-CN"), true);

        public static string MaxText(string a, string b)
        {
            return Zh.Compare(a ?? "", b ?? "") >= 0 ? (a ?? "") : (b ?? "");
        }

        public static string MaxCode(string a, string b)
        {
            return string.Compare(a ?? "", b ?? "", StringComparison.OrdinalIgnoreCase) >= 0 ? (a ?? "") : (b ?? "");
        }

        public static string Clip(string text, int max)
        {
            string value = text ?? "";
            return value.Length > max ? value.Substring(0, max) : value;
        }
    }
}
