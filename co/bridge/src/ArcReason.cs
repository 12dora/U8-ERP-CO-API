using System.Globalization;

namespace U8Co
{
    // 原因码档案（reason，表 Reason）：走 EAI（U8SrvTrans.IClsCommon，根标签 reason，ReasonXmlRs.xml），与九类档案同一条路；
    // get、list 按 EAI 标签返回（code、name、Reasontype、ReasonMemo）。Dir.xml 里 reason 是 in="y"，Distribute.xml 指向 U8SrvTrans.IclsCommon。
    // add 新增；edit 带整条记录改名称、说明；delete 只带 code；三者都回 succeed="0"。query 忽略条件、把结果写进 U8 目录的
    // 日志文件，不用。修改用实测过的 proc='edit'（ArcKind.EditProc）：桥发的本来就是整条记录（当前行打底再叠调用方字段），与 diffedit 等价。
    // 所属类型 Reasontype（iReasontype，tinyint）按 U8 EAI 模板 Reason.xml 的注释：1 不良品原因、2 让步放行原因、3 采购退货原因、
    // 4 销售退货原因、5 变更原因、6 拖欠原因；其他取值按 U8 原因码分类（如预置的 Refund 是 15 退款退货）。不良品处理单 QM05 / QM06 用 1。
    // 删除前桥先查单据引用（ArcRefs）。U8 桌面配置（Desk4Ptl.xml）里原因码档案的 cAuthId 为空，没有专门的功能 id：
    // 读取登录即可（PermRegistry），写入不登记、交给 U8。
    internal static class ArcReason
    {
        internal const string Name = "reason";
        internal const string TypeTag = "Reasontype";
        internal const string MemoTag = "ReasonMemo";
        // Reason.xml：编码 10、名称 30、说明 240；所属类型是 tinyint。
        internal const int CodeMax = 10;
        internal const int NameMax = 30;
        internal const int MemoMax = 240;
        internal const int TypeMax = 255;

        internal static bool Is(ArcKind k)
        {
            return k != null && k.Name == Name;
        }

        internal static ArcKind Kind()
        {
            ArcKind k = new ArcKind();
            k.Name = Name;
            k.Root = "reason";
            k.RsFile = "ReasonXmlRs.xml";
            k.Table = "Reason";
            k.Key = "cReasonCode";
            k.NameCol = "cReasonName";
            k.Ts = "pubufts";
            k.CodeMax = CodeMax;
            k.EditProc = "edit";
            k.Resend = new string[] { "name", TypeTag };
            return k;
        }

        // 登录前（ArcReq.Parse）的格式校验：新增必须给所属类型；名称不能全是空白；说明可以给空串清掉。
        internal static void Check(ArcReq req)
        {
            if (req.Op != "create" && req.Op != "update")
            {
                return;
            }
            string type = Get(req, TypeTag);
            if (req.Op == "create" && type == null)
            {
                throw ArcReq.Bad("新增原因码必须在 fields 给 Reasontype（所属类型，0 到 255 的整数；不良品原因是 1）",
                    FieldPath.Join("fields", TypeTag));
            }
            int n;
            if (type != null && (!int.TryParse(type, NumberStyles.None, CultureInfo.InvariantCulture, out n) || n > TypeMax))
            {
                throw ArcReq.Bad("所属类型 Reasontype 必须是 0 到 255 的整数", FieldPath.Join("fields", TypeTag));
            }
            Text(req, "name", NameMax, false);
            Text(req, MemoTag, MemoMax, true);
        }

        static string Get(ArcReq req, string tag)
        {
            return req.Fields.Get(req.Map.Canon(tag));
        }

        static void Text(ArcReq req, string tag, int max, bool blank)
        {
            string value = Get(req, tag);
            if (value == null)
            {
                return;
            }
            if (value.Length > max || (!blank && value.Trim().Length == 0))
            {
                string range = blank ? "不能超过 " : "长度必须在 1 到 ";
                throw ArcReq.Bad(tag + " " + range + max.ToString(CultureInfo.InvariantCulture) + (blank ? " 个字符" : " 之间且不能全是空白"),
                    FieldPath.Join("fields", tag));
            }
        }
    }
}
