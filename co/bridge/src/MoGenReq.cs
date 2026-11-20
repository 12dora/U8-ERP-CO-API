using System;
using System.Collections.Generic;
using System.Data;
using System.Globalization;

namespace U8Co
{
    // 生产订单参照销售订单生成（vouchers/generate，type=production_order，source_type=sale_order，ATO 模型）的登录前校验。
    // 字段名不分大小写。表头 mo_type 必填（mom_motype.MotypeCode，U8 是 nchar(2)），mo_code 可省（省略则 U8 按 MO21 规则编号），
    // remark 可省（省略时每行用 U8 列表给的销售订单行备注）。表体 1 到 200 行：source_line_id（SO_SODetails.iSOsID）、
    // quantity 必填，dept_code 可省（省略用 U8 列表给的存货生产部门）。全部行进同一张生产订单，行序按请求顺序。
    internal static class MoGenReq
    {
        public const int LinesMax = 200;
        // mom_motype.MotypeCode 是 nchar(2)，Usp_MO_GetMOGenBaseData 的 @MoTypeCode 也是 nvarchar(2)。
        const int MoTypeMax = 2;
        static readonly string[] HeadKeys = new string[] { "mo_type", "mo_code", "remark" };
        static readonly string[] LineKeys = new string[] { "source_line_id", "quantity", "dept_code" };

        internal static readonly string[] RequiredHead = new string[] { "mo_type" };
        internal static readonly string[] RequiredLine = new string[] { "source_line_id", "quantity" };

        internal static string[] HeadNames()
        {
            return (string[])HeadKeys.Clone();
        }

        internal static string[] LineNames()
        {
            return (string[])LineKeys.Clone();
        }

        // 只认参照销售订单（Kinds 里 production_order 唯一的来源）。
        // RequestsSpecs.CheckGenerate 用：只管 production_order，登录子系统同新增用 MO（kind.VerifySub）。
        internal static void CheckGenerate(WorkItem item, VoucherKind kind)
        {
            if (kind == null || kind.Name != "production_order")
            {
                return;
            }
            Check(item);
            item.SubId = kind.VerifySub;
        }

        public static void Check(WorkItem item)
        {
            CheckSource(item);
            Parse(item.Id, item.Head, item.Lines);
        }

        internal static void CheckSource(WorkItem item)
        {
            string source = item == null || item.Source == null ? "" : item.Source.Name ?? "";
            if (source != "sale_order")
            {
                throw new BridgeException(400, "bad_request", "该单据类型不能参照此来源类型生单");
            }
        }

        public static MoGenAsk Parse(int soId, Dictionary<string, object> head, object[] lines)
        {
            MoGenAsk ask = new MoGenAsk();
            ask.SoId = soId;
            try
            {
                MoCreateReq.Only(head, HeadKeys);
                ask.MoType = MoCreateReq.Str(head, "mo_type", MoTypeMax, true);
                ask.Code = MoCreateReq.Str(head, "mo_code", 30, false);
                ask.Remark = MfgReq.Raw(head, "remark") == null ? null : MoCreateReq.Str(head, "remark", 255, false);
            }
            catch (BridgeException ex)
            {
                throw FieldPath.Under(ex, "head");
            }
            if (lines == null || lines.Length < 1 || lines.Length > LinesMax)
            {
                throw BridgeException.BadField("lines", "lines 必须是 1 到 200 行");
            }
            HashSet<int> seen = new HashSet<int>();
            for (int i = 0; i < lines.Length; i++)
            {
                try
                {
                    MoGenLine line = Line(lines[i], i + 1);
                    if (!seen.Add(line.SoLineId))
                    {
                        throw BridgeException.BadField("source_line_id", "来源明细重复");
                    }
                    ask.Lines.Add(line);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, "lines", i);
                }
            }
            return ask;
        }

        static MoGenLine Line(object raw, int seq)
        {
            Dictionary<string, object> row = raw as Dictionary<string, object>;
            if (row == null)
            {
                throw new BridgeException(400, "bad_request", "lines 的每一行必须是对象");
            }
            MoCreateReq.Only(row, LineKeys);
            MoGenLine line = new MoGenLine();
            line.Seq = seq;
            line.SoLineId = MfgReq.LineId(row);
            line.Qty = MfgReq.LineQty(row);
            line.Dept = MoCreateReq.Str(row, "dept_code", 12, false);
            return line;
        }
    }

    internal sealed class MoGenAsk
    {
        public int SoId;
        public string MoType = "";
        public string Code = "";
        // null：请求没带 remark，用 U8 列表的行备注（销售订单行 cMemo）；空串也是调用方给的值。
        public string Remark;
        public List<MoGenLine> Lines = new List<MoGenLine>();
        // 登录后由 MoGenSql 填：销售订单号、类别 id、存货数量 / 件数小数位、调用前的最大 MoId / MoDId 和数据库时间。
        public string SoCode = "";
        public int MoTypeId;
        public int QtyDigits = 6;
        public int AuxDigits = 6;
        // 生产制造选项「允许超计划下达」（mom_parameter.RlsOverPlanFlag，空值按允许，同 U8 MParameters）。
        public bool OverPlan;
        // 操作员受数据权限控制（不是账套主管）：U8 列表里少了行时按数据权限报 403（MoGenArgs.Attach）。
        public bool DataAuth;
        public int MaxMoId;
        public int MaxMoDId;
        public string Since = "";
    }

    internal sealed class MoGenLine
    {
        public int Seq;
        public int SoLineId;
        public decimal Qty;
        // Dept：请求给的生产部门（可空）；FinalDept：最终送给 U8 的（请求的，或 U8 列表的缺省）。
        public string Dept = "";
        public string FinalDept = "";
        // 闸门读到的：存货编码、调用前的 SO_SODetails.iMoQuantity。
        public string InvCode = "";
        public decimal MoQtyBefore;
        // U8 GetMOGenBaseData 列表里这一行（MOGenBaseData 表的 DataRow）。
        public DataRow U8Row;

        public string SeqText
        {
            get { return Seq.ToString(CultureInfo.InvariantCulture); }
        }

        public string QtyText
        {
            get { return Qty.ToString("0.######", CultureInfo.InvariantCulture); }
        }
    }
}
