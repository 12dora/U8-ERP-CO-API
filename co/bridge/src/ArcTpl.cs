using System;
using System.Collections.Generic;

namespace U8Co
{
    // 新增：按模板档案的当前值铺底（经 RsXml 标签 → 列名），再叠调用方字段和缺省值。
    internal static class ArcTpl
    {
        const string GradeSql = "SELECT CODINGRULE FROM GradeDef_Base WHERE KEYWORD=? AND iYear=0";

        // 跳过：编码、名称、建档/变更人和日期、不可写的标签、银行账号与联系方式等私有标签、与模板编码绑定的标签、表里没有的列、空值；
        // 同一列只取第一个标签。
        public static void Fill(object conn, ArcReq req, ArcBag bag)
        {
            Dictionary<string, string> row = ArcRead.Row(conn, req.Kind, req.Template);
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "模板档案不存在：" + req.Template);
            }
            Copy(req, row, bag, true);
        }

        // 修改：U8 在 diffedit 时也按档案设置检查必输项，所以把当前行所有可写、非空的标签都带上（跳过不可写的和 U8 自己维护的）。
        public static void Current(ArcReq req, Dictionary<string, string> row, ArcBag bag)
        {
            Copy(req, row, bag, false);
        }

        static void Copy(ArcReq req, Dictionary<string, string> row, ArcBag bag, bool template)
        {
            HashSet<string> columns = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            IList<string> tags = req.Map.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                string tag = tags[i];
                bool skip = template ? req.Kind.TplSkipped(tag) : req.Kind.UpdSkipped(tag);
                string column = req.Map.Column(tag);
                string value;
                if (skip || !row.TryGetValue(column, out value) || value.Trim().Length == 0)
                {
                    continue;
                }
                if (columns.Add(column))
                {
                    bag.Put(tag, value);
                }
            }
        }

        // 缺省值只补调用方没给的；日期、级次、末级标志即使模板里有也按新档案重算。
        public static void Defaults(WorkContext ctx, ArcReq req, ArcBag bag)
        {
            ArcKind k = req.Kind;
            Override(req, bag, k.DateTag, ctx.Item.Date);
            for (int i = 0; i + 1 < k.Defaults.Length; i += 2)
            {
                Missing(req, bag, k.Defaults[i], DefaultValue(ctx, k.Defaults[i + 1]));
            }
            for (int i = 0; i + 1 < k.Copies.Length; i += 2)
            {
                string source = req.Map.Canon(k.Copies[i + 1]);
                Missing(req, bag, k.Copies[i], bag.Get(source));
            }
            OneOf(req, bag);
            if (k.Grade != null)
            {
                int rank = Rank(ctx.Conn, k.Grade, req.Code);
                Override(req, bag, k.RankTag, rank > 0 ? rank.ToString(System.Globalization.CultureInfo.InvariantCulture) : null);
                Override(req, bag, k.EndTag, "1");
            }
        }

        // 缺省值表里的本位币占位换成账套的本位币（WorkContext.HomeCurrency）。
        static string DefaultValue(WorkContext ctx, string value)
        {
            return value == AccDefaults.HomeToken ? ctx.HomeCurrency : value;
        }

        // 采购/委外/服务至少一个：都没出现时第一个置 1。
        static void OneOf(ArcReq req, ArcBag bag)
        {
            string[] names = req.Kind.OneOf;
            if (names.Length == 0)
            {
                return;
            }
            for (int i = 0; i < names.Length; i++)
            {
                if (bag.Has(names[i]))
                {
                    return;
                }
            }
            Missing(req, bag, names[0], "1");
        }

        // 调用方没给就用 value（模板值也被替换）。
        static void Override(ArcReq req, ArcBag bag, string name, string value)
        {
            string tag = req.Map.Canon(name);
            if (tag == null || value == null || req.Fields.Has(tag))
            {
                return;
            }
            bag.Put(tag, value);
        }

        // 调用方和模板都没给才用 value。
        static void Missing(ArcReq req, ArcBag bag, string name, string value)
        {
            string tag = req.Map.Canon(name);
            if (tag == null || value == null || bag.Has(tag))
            {
                return;
            }
            bag.Put(tag, value);
        }

        // CODINGRULE 每位是一级的长度（如 22222、32、5）；编码长度正好落在第 n 级末尾时级次为 n，否则 0（交给 U8 判断）。
        static int Rank(object conn, string keyword, string code)
        {
            string rule = Rows.Scalar(conn, GradeSql, new object[] { keyword });
            if (rule == null)
            {
                return 0;
            }
            int total = 0;
            int level = 0;
            for (int i = 0; i < rule.Length; i++)
            {
                char c = rule[i];
                if (c < '1' || c > '9')
                {
                    continue;
                }
                total += c - '0';
                level++;
                if (total == code.Length)
                {
                    return level;
                }
            }
            return 0;
        }
    }
}
