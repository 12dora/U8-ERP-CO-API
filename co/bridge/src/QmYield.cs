using System;

namespace U8Co
{
    // 检验单的让步接收核准人：表头 CYIELDERCODE（人员编码）、CYIELDERNAME（姓名，桥按人员档案写）、DYIELDDATE（核准日期）。
    // 模板 VT 353 / 354 表头的「让步接收核准人编码 / 让步接收核准人 / 让步接收核准日期」，视图 QM_QARRCHECKB / QM_QPROCHECKB 都有这三列。
    // 产品检验单有让步数量时缺了核准人，U8 报「'让步接收核准人'不能为空」（已在测试账套核对），来料检验单可能同样要求。
    // 来料 / 产品检验单（生单、修改）让步数量大于 0 时桥在调用前要求核准人；其他检验单只写不要求（未实测）。
    // 让步数量改回 0 时桥不清这三列，交给 U8 判断。
    internal static class QmYield
    {
        public const string CodeKey = "cyieldercode";
        public const string DateKey = "dyielddate";

        // 让步数量大于 0 时必须有核准人：请求送了，或单据上已有（修改）。field 是 400 的字段路径。
        internal static void Require(string code, string existing, decimal con, bool required, string field)
        {
            if (!required || con <= 0m || (code ?? "").Length > 0 || (existing ?? "").Trim().Length > 0)
            {
                return;
            }
            throw BridgeException.BadField(field, "有让步接收数量时必须指定让步接收核准人 cyieldercode（人员编码）");
        }

        // {编码, 姓名, 核准日期}；没送编码时编码、姓名为空。核准日期没送时取 fallback（请求的单据日期或登录日期）。
        internal static string[] Resolve(object conn, string code, string date, string fallback, string prefix)
        {
            string name = "";
            if ((code ?? "").Length > 0)
            {
                string found = Rows.Scalar(conn, QmSql.PersonSql, new object[] { code });
                if (found == null)
                {
                    throw BridgeException.BadField(prefix + CodeKey, "让步接收核准人不存在 " + code);
                }
                name = found.Trim();
            }
            string day = (date ?? "").Length > 0 ? date : (fallback ?? "").Trim();
            return new string[] { code ?? "", name, day };
        }

        // 请求里的核准日期：送了就必须是 yyyy-MM-dd。
        internal static void CheckDate(string date, string field)
        {
            DateTime parsed;
            if ((date ?? "").Length > 0 && !DateTime.TryParseExact(date, "yyyy-MM-dd",
                System.Globalization.CultureInfo.InvariantCulture, System.Globalization.DateTimeStyles.None, out parsed))
            {
                throw BridgeException.BadField(field, "让步接收核准日期必须是 yyyy-MM-dd");
            }
        }
    }
}
