using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 物料清单（bom）写给 U8 的一行子件。新增按实测的 B2 缺省（与 U8 界面新增的 BOM 一致），
    // 修改先按库里的现值打底（BomRead.RowOf），再叠调用方这次送来的字段（Apply）。
    // 标志不送 U8 会在 SaveBOM 里报 CheckStructureIntegrity，所以每行都要带全（docs/u8-notes.md「物料清单」）。
    internal sealed class BomRow
    {
        public const string EndDate = "2099-12-31";
        public int Seq;
        public string OpSeq = "0000";
        public string InvCode = "";
        public decimal QtyN;
        public decimal QtyD = 1m;
        public decimal Scrap;
        public int Wip = 3;
        public string Wh = "";
        public string Remark = "";
        public string EffBeg = "";
        public string EffEnd = EndDate;
        public int FvFlag = 1;
        public int Offset;
        public decimal PlanRate = 100m;
        public int Byproduct;
        public int AccuCost = 1;
        public int Optional;
        public int Mutex = 2;
        public int ProductType = 1;
        public int CostWip;

        // 调用方的字段（BomReq 已校验、键为小写）叠到本行上。行号、存货只在新增行上出现。
        public void Apply(Dictionary<string, object> fields)
        {
            foreach (KeyValuePair<string, object> kv in fields)
            {
                Set(kv.Key, kv.Value);
            }
        }

        void Set(string key, object value)
        {
            switch (key)
            {
                case "sort_seq":
                    Seq = (int)value;
                    return;
                case "inv_code":
                    InvCode = (string)value;
                    return;
                case "op_seq":
                    OpSeq = (string)value;
                    return;
                case "wh_code":
                    Wh = (string)value;
                    return;
                case "remark":
                    Remark = (string)value;
                    return;
                case "wip_type":
                    Wip = (int)value;
                    return;
            }
            SetNumber(key, (decimal)value);
        }

        // 写后回读核对：回读行（BomRead.RowOf）与要写的行逐项相同——桥送给 U8 的每个字段都要对上
        // （DCompScrapFlag 除外：U8 的 _AddBomDetails 不读它）。编码去空格、存货和仓库不分大小写。
        public bool SameAs(BomRow want)
        {
            return Seq == want.Seq && SameText(want) && QtyN == want.QtyN && QtyD == want.QtyD && Scrap == want.Scrap
                && Wip == want.Wip && EffBeg == want.EffBeg && EffEnd == want.EffEnd && SameFlags(want);
        }

        bool SameText(BomRow want)
        {
            return OpSeq.Trim() == want.OpSeq.Trim()
                && string.Equals(InvCode.Trim(), want.InvCode.Trim(), StringComparison.OrdinalIgnoreCase)
                && string.Equals(Wh.Trim(), want.Wh.Trim(), StringComparison.OrdinalIgnoreCase)
                && Remark.Trim() == want.Remark.Trim();
        }

        bool SameFlags(BomRow want)
        {
            return FvFlag == want.FvFlag && Offset == want.Offset && PlanRate == want.PlanRate && Byproduct == want.Byproduct
                && AccuCost == want.AccuCost && Optional == want.Optional && Mutex == want.Mutex
                && ProductType == want.ProductType && CostWip == want.CostWip;
        }

        void SetNumber(string key, decimal value)
        {
            if (key == "base_qty_n")
            {
                QtyN = value;
            }
            else if (key == "base_qty_d")
            {
                QtyD = value;
            }
            else if (key == "comp_scrap")
            {
                Scrap = value;
            }
        }
    }

    // 请求里的一行：op（新增时都是 add；修改是 add / update / delete）、行号（0 表示没给）、已校验的其余字段。
    internal sealed class BomChange
    {
        public string Op = "add";
        public int Seq;
        public Dictionary<string, object> Fields = new Dictionary<string, object>(StringComparer.Ordinal);
    }

    // 一次新增或修改。登录前由 BomReq 填请求部分；登录后由 BomSql / BomEdit 填母件、版本和最终要写的行。
    internal sealed class BomAsk
    {
        public string InvCode = "";
        // 0：新增时按该母件的最大版本加版本增量（mom_parameter.VersionIncrement，读不到按 10）。
        public int Version;
        public Dictionary<string, object> Head = new Dictionary<string, object>(StringComparer.Ordinal);
        public List<BomChange> Changes = new List<BomChange>();
        public int PartId;
        // 调用前的数据库时间（style 121）：拿不到调用结果时按它认本次新建的版本。
        public string Since = "";
        public string VersionDesc = "";
        public string EffDate = "";
        public decimal ParentScrap;
        public List<BomRow> Rows = new List<BomRow>();
        // 修改前已有的行号（BomMerge.Merge 填）。
        public HashSet<int> Existing = new HashSet<int>();

        public string VersionText
        {
            get { return Version.ToString(CultureInfo.InvariantCulture); }
        }

        public string HeadText(string key)
        {
            object value;
            return Head.TryGetValue(key, out value) ? value as string : null;
        }
    }
}
