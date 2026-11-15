using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 设备台账（equipment，U8 设备管理 EQ_EQData）：读取同其他按表列名返回的档案（ArcReadRo，编码 cEQCode、
    // 名称 cEQName、rowversion ufts）；新增走 U8 官方 EAI roottag eqdata（U8EQEAI.clsEQEAI，经 EaiDistribute 分发）。
    // U8EQEAI 只有 INSERT（组件里没有修改、删除的 SQL），所以修改、删除 400，请在 U8 客户端处理。
    // 字段名就是 EAI 标签（Template\EQData.xml），名称用 name；制单人 cmaker、制单日期 dtdate 由桥填（操作员名称、登录日期），
    // 审核人、审核日期、模板号不收。设备台账按静态台账处理（不含变动、运行记录），这里不做审核。
    internal static class ArcEq
    {
        internal const string Name = "equipment";
        internal const string Root = "eqdata";
        // EQ_EQData.cEQCode nvarchar(30)。
        internal const int CodeMax = 30;
        internal const string UpdateText = "设备台账的 U8 官方导入（EAI eqdata）只支持新增，修改请在 U8 客户端处理";

        // 报文次序照模板；name 发 ceqname，cmaker、dtdate 由桥补。
        static readonly string[] Order = new string[]
        {
            "ceqname", "cabccode", "ceqtypecode", "csupeqcode", "cstacode", "cpcode", "cdepcode", "cseq", "cpiccode", "cvencode",
            "dtccdate", "dtgmdate", "dtazdate", "dtsydate", "intsynx", "dtbxdate", "dbldjgl", "intdjnum", "cdjdw", "dblzdsj",
            "cassetnum", "cmaker", "dtdate", "cmemo"
        };
        static readonly string[] Dates = new string[] { "dtccdate", "dtgmdate", "dtazdate", "dtsydate", "dtbxdate", "cdefine4", "cdefine6" };
        static readonly string[] Numbers = new string[] { "intsynx", "dbldjgl", "dblzdsj", "cdefine7", "cdefine16" };
        static readonly string[] Ints = new string[] { "intdjnum", "cdefine5", "cdefine15" };
        const string ExistsSql = "SELECT TOP 1 cEQCode AS c FROM EQ_EQData WHERE cEQCode=?";
        const string NameSql = "SELECT TOP 1 cEQName AS n FROM EQ_EQData WHERE cEQCode=?";

        internal static bool Is(ArcKind kind)
        {
            return kind != null && kind.Name == Name;
        }

        internal static ArcKind Kind(ArcKind k)
        {
            k.ReadOnly = false;
            k.NoUpdate = true;
            k.NoUpdateText = UpdateText;
            k.NoDelete = true;
            List<string> pairs = new List<string>();
            pairs.Add("name");
            pairs.Add("cEQName");
            for (int i = 1; i < Order.Length; i++)
            {
                if (Order[i] != "cmaker" && Order[i] != "dtdate")
                {
                    pairs.Add(Order[i]);
                    pairs.Add(Order[i]);
                }
            }
            for (int n = 1; n <= 16; n++)
            {
                string tag = "cdefine" + n.ToString(CultureInfo.InvariantCulture);
                pairs.Add(tag);
                pairs.Add(tag);
            }
            k.SqlMap = ArcMap.Fixed(pairs.ToArray());
            return k;
        }

        // 登录前：日期写成 yyyy-mm-dd，数字、整数能解析；不收 template。
        internal static void Check(ArcReq req)
        {
            if (req.Op != "create")
            {
                return;
            }
            if (req.Template != null)
            {
                throw ArcReq.Bad("档案 equipment 不支持 template", "template");
            }
            foreach (string tag in req.Fields.Tags)
            {
                string value = req.Fields.Get(tag);
                string at = FieldPath.Join("fields", tag);
                if (value == null || value.Trim().Length == 0)
                {
                    continue;
                }
                if (Array.IndexOf(Dates, tag) >= 0)
                {
                    FaCardReq.Day(value.Trim(), tag, at);
                }
                else if (Array.IndexOf(Numbers, tag) >= 0 || Array.IndexOf(Ints, tag) >= 0)
                {
                    Number(value.Trim(), tag, Array.IndexOf(Ints, tag) >= 0, at);
                }
            }
        }

        static void Number(string text, string tag, bool whole, string at)
        {
            decimal value;
            if (!decimal.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out value)
                || (whole && (value != decimal.Truncate(value) || value < int.MinValue || value > int.MaxValue)))
            {
                throw ArcReq.Bad("字段 " + tag + (whole ? " 必须是整数" : " 必须是数字"), at);
            }
        }

        public static ApiResult Write(WorkContext ctx, ArcReq req)
        {
            ArcGuard.Permit(ctx, req);
            if (Rows.Scalar(ctx.Conn, ExistsSql, new object[] { req.Code }) != null)
            {
                throw ArcGuard.State("设备编码 " + req.Code + " 已存在");
            }
            string maker = ctx.Session == null || ctx.Session.OperatorName == null ? "" : ctx.Session.OperatorName.Trim();
            string xml = Envelope(req.Code, req.Fields, maker, ctx.Item.Date.Trim());
            string name = req.Fields.Get("name").Trim();
            return ArcPartnerRun.Eai(ctx, req, "add", EaiDistribute.What, delegate { return EaiDistribute.Call(ctx, xml); },
                delegate(object fresh)
                {
                    string now = Rows.Scalar(fresh, NameSql, new object[] { req.Code });
                    return now != null && string.Equals(now.Trim(), name, StringComparison.Ordinal);
                });
        }

        // 报文：<ufinterface roottag='eqdata' … proc='add'><eqdata><header>…</header></eqdata></ufinterface>。
        internal static string Envelope(string code, ArcBag fields, string maker, string day)
        {
            StringBuilder sb = EaiDistribute.Begin(Root, "add");
            sb.Append("<header>");
            ArcPartnerXml.Tag(sb, "ceqcode", code);
            for (int i = 0; i < Order.Length; i++)
            {
                string value = Order[i] == "ceqname" ? fields.Get("name") : Order[i] == "cmaker" ? maker
                    : Order[i] == "dtdate" ? day : fields.Get(Order[i]);
                if (value != null && value.Trim().Length > 0)
                {
                    ArcPartnerXml.Tag(sb, Order[i], value.Trim());
                }
            }
            for (int n = 1; n <= 16; n++)
            {
                string tag = "cdefine" + n.ToString(CultureInfo.InvariantCulture);
                string value = fields.Get(tag);
                if (value != null && value.Trim().Length > 0)
                {
                    ArcPartnerXml.Tag(sb, tag, value.Trim());
                }
            }
            sb.Append("</header>");
            return EaiDistribute.End(sb, Root);
        }
    }
}
