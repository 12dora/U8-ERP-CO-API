using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 一种汇率：exch.itype（1 浮动、2 记账 / 固定、3 调整）、请求里的标签和值、EAI 报文的 date。
    internal sealed class ExchPlan
    {
        public int Type;
        public string Tag;
        public string Value;
        public string Date;
    }

    // 汇率（exchange_rate，表 exch）写入。编码与读取相同："<币种>:<年度>:<期间>" 是固定汇率的一个期间
    // （fields：rate 记账汇率 itype 2、adjust_rate 调整汇率 itype 3），"<币种>:<年度>:<期间>:<日>" 是一天的浮动汇率（rate，itype 1）。
    // 新增走 U8 官方 EAI 分发器（roottag currencyrate → U8PzInsert.icurrencyrate）：报文没有年度，U8 写登录年度，
    // 所以编码的年度必须是登录年度；同一键已存在时 U8 回「导入完成！」却不改汇率，桥先查、存在就 409，改用 update。
    // 一次新增只写一种汇率（rate 或 adjust_rate），免得第二种被拒时第一种已自行提交。
    // proc='edit' 实测回「暂不提供此项功能」：修改、删除走受控 SQL（ArcExchSql）。
    // 闸门（ArcExchSql.Gate）：币种必须存在；本位币没有汇率；总账该年度该期间已结账（GL_mend.bflag）时不能写。
    internal static class ArcExchWrite
    {
        internal const string RateTag = "rate";
        internal const string AdjustTag = "adjust_rate";
        internal const string Root = "currencyrate";
        // 汇率上限：exch.nflat 是 float，这里只挡明显错误的值。
        const decimal MaxRate = 1000000000m;

        internal static ArcKind Kind(ArcKind k)
        {
            k.ReadOnly = false;
            k.NameTag = null;
            k.SqlMap = ArcMap.Fixed(new string[] { RateTag, RateTag, AdjustTag, AdjustTag });
            return k;
        }

        // 登录前（ArcReq.Parse）的格式校验；库里的状态在 Write 里查。
        internal static void Check(ArcReq req)
        {
            if (req.Op == "get")
            {
                return;
            }
            ExchKey key = ExchKey.Parse(req.Code, "code");
            if (key.Date != null)
            {
                throw ArcReq.Bad("写入汇率时 code 必须写成 <币种>:<年度>:<期间>（固定汇率）或 <币种>:<年度>:<期间>:<日>（浮动汇率）", "code");
            }
            if (req.Template != null)
            {
                throw ArcReq.Bad("档案 exchange_rate 不支持 template", "template");
            }
            if (req.Op == "delete")
            {
                return;
            }
            Rate(req, RateTag);
            Rate(req, AdjustTag);
            if (key.Day != null && req.Fields.Has(AdjustTag))
            {
                throw ArcReq.Bad("浮动汇率没有调整汇率 adjust_rate", "fields.adjust_rate");
            }
            if (req.Op != "create")
            {
                return;
            }
            if (req.Fields.Has(RateTag) == req.Fields.Has(AdjustTag))
            {
                throw ArcReq.Bad("新增一次只写一种汇率：fields 给 rate（记账汇率，浮动汇率是当日汇率）或 adjust_rate（调整汇率）之一", "fields");
            }
            if (key.Day != null)
            {
                CheckDay(key);
            }
        }

        // 汇率：大于 0、不超过 1000000000 的数。
        static void Rate(ArcReq req, string tag)
        {
            string text = req.Fields.Get(tag);
            if (text == null)
            {
                return;
            }
            decimal value;
            if (!decimal.TryParse(text.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out value) || value <= 0m || value > MaxRate)
            {
                throw ArcReq.Bad("字段 " + tag + " 必须是大于 0、不超过 1000000000 的数", FieldPath.Join("fields", tag));
            }
        }

        // 新增浮动汇率：日写成 yyyy-mm-dd，落在编码的年度、期间（自然月，同 ExchKey）里。
        static void CheckDay(ExchKey key)
        {
            DateTime day;
            if (!DateTime.TryParseExact(key.Day, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day)
                || day.Year != key.Year || day.Month != key.Period)
            {
                throw ArcReq.Bad("新增浮动汇率时 code 写成 <币种>:<年度>:<期间>:<yyyy-mm-dd>，日期在该年度、期间内", "code");
            }
        }

        public static ApiResult Write(WorkContext ctx, ArcReq req)
        {
            ArcGuard.Permit(ctx, req);
            ExchKey key = ExchKey.Parse(req.Code, "code");
            if (req.Op != "create")
            {
                return ArcExchSql.Write(ctx, req, key);
            }
            return Create(ctx, req, key);
        }

        static ApiResult Create(WorkContext ctx, ArcReq req, ExchKey key)
        {
            int login = GlState.LoginYear(ctx);
            if (key.Year != login)
            {
                throw ArcReq.Bad("U8 按登录日期的年度写汇率：新增时 code 的年度必须是 " + login.ToString(CultureInfo.InvariantCulture), "code");
            }
            object conn = ctx.Conn;
            ArcExchSql.Gate(conn, req, key);
            ExchPlan plan = Plans(req, key)[0];
            if (ArcExchSql.Find(conn, key, plan.Type).Count > 0)
            {
                throw ArcGuard.State("汇率 " + req.Code + " 的" + Label(plan.Type) + "已存在，请用 update 修改");
            }
            string xml = Envelope(key, plan);
            return ArcPartnerRun.Eai(ctx, req, "add", EaiDistribute.What, delegate { return EaiDistribute.Call(ctx, xml); },
                delegate(object fresh) { return ArcExchSql.Created(fresh, req, key, plan); });
        }

        // 请求里给了的汇率，按 rate、adjust_rate 的次序。
        internal static List<ExchPlan> Plans(ArcReq req, ExchKey key)
        {
            List<ExchPlan> list = new List<ExchPlan>();
            Add(list, req, RateTag, key.Day != null ? 1 : 2, key);
            Add(list, req, AdjustTag, 3, key);
            return list;
        }

        static void Add(List<ExchPlan> list, ArcReq req, string tag, int type, ExchKey key)
        {
            string text = req.Fields.Get(tag);
            if (text == null)
            {
                return;
            }
            ExchPlan plan = new ExchPlan();
            plan.Type = type;
            plan.Tag = tag;
            plan.Value = decimal.Parse(text.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture).ToString(CultureInfo.InvariantCulture);
            // 模板说明：浮动汇率的 date 是自然日期，其余是期间号（记账汇率的 cdate 存期间号，如 '10'）。
            plan.Date = type == 1 ? key.Day : key.Period.ToString(CultureInfo.InvariantCulture);
            list.Add(plan);
        }

        internal static string Label(int type)
        {
            return type == 3 ? "调整汇率" : type == 1 ? "浮动汇率" : "记账汇率";
        }

        // 报文（Template\CurrencyRate.xml）：name 币种、period 会计期间、type、date、rate；不发 id（U8 自动编号）。
        internal static string Envelope(ExchKey key, ExchPlan plan)
        {
            StringBuilder sb = EaiDistribute.Begin(Root, "add");
            ArcPartnerXml.Tag(sb, "name", key.Currency);
            ArcPartnerXml.Tag(sb, "period", key.Period.ToString(CultureInfo.InvariantCulture));
            ArcPartnerXml.Tag(sb, "type", plan.Type.ToString(CultureInfo.InvariantCulture));
            ArcPartnerXml.Tag(sb, "date", plan.Date);
            ArcPartnerXml.Tag(sb, "rate", plan.Value);
            return EaiDistribute.End(sb, Root);
        }
    }
}
