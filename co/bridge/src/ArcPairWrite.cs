using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 两列主键可写档案的新增校验（修改、删除没有额外条件，存在与否由 ArcWrite 查）。
    // 自定义项档案：自定义项号要在 UserDef_Base 里、设成「需要建档」（bArchive=1）、不取自其他档案（cRelArchive 为空），
    // 档案值不超过使用长度 iLength（定长 bFixLength=1 时必须等长）。客户存货对照：客户、存货都要存在。
    internal static class ArcPairWrite
    {
        internal const string Define = "user_define";
        internal const string Contra = "customer_inventory";
        const string DefSql = "SELECT bArchive AS arc, iLength AS len, bFixLength AS fix, ISNULL(cRelArchive, N'') AS rel"
            + " FROM UserDef_Base WHERE cID=?";
        const string CusSql = "SELECT cCusCode FROM Customer WHERE cCusCode=?";
        const string InvSql = "SELECT cInvCode FROM Inventory WHERE cInvCode=?";

        public static void Check(object conn, ArcReq req)
        {
            if (req.Op != "create")
            {
                return;
            }
            object[] parts = ArcPair.KeyArgs(req.Kind, req.Code);
            string first = (string)parts[0];
            string second = (string)parts[1];
            if (req.Kind.Name == Define)
            {
                CheckDefine(conn, first, second);
                return;
            }
            if (Rows.Scalar(conn, CusSql, new object[] { first }) == null)
            {
                throw ArcReq.Bad("客户 " + first + " 不存在");
            }
            if (Rows.Scalar(conn, InvSql, new object[] { second }) == null)
            {
                throw ArcReq.Bad("存货 " + second + " 不存在");
            }
        }

        static void CheckDefine(object conn, string id, string value)
        {
            Dictionary<string, object> def = Rows.One(conn, DefSql, new object[] { id });
            if (def == null)
            {
                throw ArcReq.Bad("自定义项 " + id + " 不存在");
            }
            if (ArcRead.Cell(def, "arc") != "1")
            {
                throw ArcReq.Bad("自定义项 " + id + " 没有设置为需要建档，不能维护档案值");
            }
            string rel = (ArcRead.Cell(def, "rel") ?? "").Trim();
            if (rel.Length > 0)
            {
                throw ArcReq.Bad("自定义项 " + id + " 的值取自档案 " + rel + "，请维护那个档案");
            }
            CheckLength(def, id, value);
        }

        // 未覆盖：iLength 按字符还是按字节计（桥按字符）。
        static void CheckLength(Dictionary<string, object> def, string id, string value)
        {
            int len;
            string text = ArcRead.Cell(def, "len");
            if (text == null || !int.TryParse(text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out len) || len <= 0)
            {
                return;
            }
            string limit = len.ToString(CultureInfo.InvariantCulture);
            if (value.Length > len)
            {
                throw ArcReq.Bad("档案值超过自定义项 " + id + " 的使用长度 " + limit);
            }
            if (ArcRead.Cell(def, "fix") == "1" && value.Length != len)
            {
                throw ArcReq.Bad("自定义项 " + id + " 是定长 " + limit + " 个字符");
            }
        }
    }
}
