using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 红字采购入库单（退库）无来源新增（未经实测）：vouchers/create type=purchase_in，表头 red=true。
    // 请求里数量填正数，桥按蓝字的规则建 DOM（StockDom.PurHead / PurBody：供应商、采购类型、批号、货位、单价）后
    // 表头写 bredvouch=1，表体数量、件数和各金额列取负（单价不变，同账套里 U8 自己录的手工红字退库），
    // Insert("01") 第 11 个参数 bIsRedVouch 传 true。「普通业务必有订单」（PU.bPTHavePO）只拦蓝字：
    // 选项打开的账套里仍有 U8 客户端录入的无来源红字退库（cSource=库存、bredvouch=1），红字照 U8 放行。
    // 没有来源，不回写。删除、审核、弃审走普通路径（不拒绝红字）；修改仍拒绝（StockPurIn.RefuseEditState）。
    internal static partial class StockCo
    {
        const string RedKey = "red";
        static readonly string[] RedNegative = new string[]
        {
            "iQuantity", "iNum", "iOriMoney", "iOriTaxPrice", "ioriSum", "iPrice", "iTaxPrice", "iSum"
        };

        // 表头 red：true 红字，false 或没有是蓝字；其它值 400。
        internal static bool RedHead(Dictionary<string, object> head)
        {
            object value = MfgReq.Raw(head, RedKey);
            if (value == null)
            {
                return false;
            }
            if (!(value is bool))
            {
                throw new BridgeException(400, "bad_request", "red 必须是布尔");
            }
            return (bool)value;
        }

        // meta：采购入库新增的表头 exact 名单加上 red（MetaFields 的字段全集里没有这个控制键）。
        internal static void RedMeta(VoucherKind kind, Dictionary<string, object> spec)
        {
            if (kind == null || kind.Name != "purchase_in" || spec == null)
            {
                return;
            }
            Dictionary<string, object> head = spec.ContainsKey("head") ? spec["head"] as Dictionary<string, object> : null;
            string[] exact = head == null ? null : head["exact"] as string[];
            if (exact == null || Array.IndexOf(exact, RedKey) >= 0)
            {
                return;
            }
            List<string> all = new List<string>(exact);
            all.Add(RedKey);
            head["exact"] = all.ToArray();
        }

        // 去掉 red 之后的表头（其余字段照原样交给 StockDom 的白名单）。
        internal static Dictionary<string, object> WithoutRed(Dictionary<string, object> head)
        {
            if (head == null)
            {
                return null;
            }
            Dictionary<string, object> rest = new Dictionary<string, object>();
            foreach (KeyValuePair<string, object> kv in head)
            {
                if (!string.Equals(kv.Key, RedKey, StringComparison.OrdinalIgnoreCase))
                {
                    rest[kv.Key] = kv.Value;
                }
            }
            return rest;
        }

        internal static ApiResult CreateRedPurIn(WorkContext ctx, VoucherKind kind,
            Dictionary<string, object> head, object[] lines)
        {
            RequireSt(kind);
            StockPurInPos.CheckCreate(ctx.Conn, head, lines);
            StockGen.CheckRedIn(ctx.Conn, head, lines);
            object co = null;
            object domH = null;
            object domB = null;
            object pos = null;
            object msg = null;
            try
            {
                string maker = ctx.Session.OperatorName == null ? "" : ctx.Session.OperatorName.Trim();
                string billDate = ctx.Item == null || ctx.Item.Date == null ? "" : ctx.Item.Date.Trim();
                domH = StockDom.BuildHead(ctx, kind, head, maker, billDate);
                StockDom.SetHeadValue(domH, "bredvouch", "1");
                domB = StockDom.BuildBody(ctx.Conn, kind, lines);
                NegateBody(domB);
                pos = Rows.NewDom();
                msg = Rows.NewDom();
                co = StockCall.OpenCo(ctx);
                string code = StockCall.NewCode(ctx, co, kind, HeadSeeds(domH));
                StockDom.SetHeadValue(domH, "cCode", code);
                int[] refs;
                object[] args = StockCall.InsertArgs(kind, StockCall.Forms(domH, domB, pos, ctx.Conn, msg), out refs);
                args[10] = true;
                StockCall.RunCo(ctx, co, "Insert", args, refs, null);
                ApiResult made = Inserted(ctx, kind, code, args[6]);
                ConfirmRed(ctx, made);
                return made;
            }
            finally
            {
                ComUtil.Final(msg);
                ComUtil.Final(pos);
                ComUtil.Final(domB);
                ComUtil.Final(domH);
                ComUtil.Final(co);
            }
        }

        const string RedStateSql = "select convert(varchar(5), isnull(h.bredvouch,0)) as red,"
            + " (select count(*) from rdrecords01 b where b.ID=h.ID) as n,"
            + " (select count(*) from rdrecords01 b where b.ID=h.ID and isnull(b.iQuantity,0)>=0) as notneg"
            + " from RdRecord01 h where h.ID=?";

        // 提交后在新连接上确认是红字（bredvouch=1）、每行数量为负；否则 504，带上新主键。
        static void ConfirmRed(WorkContext ctx, ApiResult made)
        {
            int id = MadeId(made);
            string why = "";
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                Dictionary<string, object> row = Rows.One(conn, RedStateSql, new object[] { id });
                if (!RedOk(row))
                {
                    why = "保存后不是红字或数量不全为负";
                }
            }
            catch (Exception ex)
            {
                why = "回读失败：" + ex.Message;
            }
            finally
            {
                AdoXml.Close(conn);
            }
            if (why.Length > 0)
            {
                CoRows.Note(ctx.Item, "红字采购入库 " + id.ToString(CultureInfo.InvariantCulture) + " " + why);
                throw new BridgeException(504, "outcome_unknown",
                    "红字采购入库单已保存（id " + id.ToString(CultureInfo.InvariantCulture) + "），但回读未确认是红字，请到 U8 核对");
            }
        }

        static int MadeId(ApiResult made)
        {
            if (made == null || made.Body == null || !made.Body.ContainsKey("id"))
            {
                return 0;
            }
            return CoRows.AsId(Values.Text(made.Body["id"]));
        }

        static bool RedOk(Dictionary<string, object> row)
        {
            return row != null && CoRows.FlagOf(row, "red") && CoRows.Col(row, "n") != "0" && CoRows.Col(row, "notneg") == "0";
        }

        // 数量、件数、原币与本币的无税金额、税额、价税合计取负；空的列不动。
        static void NegateBody(object dom)
        {
            List<object> rows = DomRows.RowsOf(dom);
            try
            {
                List<string> names = DomRows.Schema(dom);
                for (int i = 0; i < rows.Count; i++)
                {
                    for (int j = 0; j < RedNegative.Length; j++)
                    {
                        NegateCell(dom, rows[i], RedNegative[j], names);
                    }
                }
            }
            finally
            {
                for (int i = 0; i < rows.Count; i++)
                {
                    ComUtil.ReleaseOne(rows[i]);
                }
            }
        }

        static void NegateCell(object dom, object row, string name, List<string> names)
        {
            decimal value;
            if (!StockUnits.Dec(DomRows.Get(row, name), out value) || value == 0m)
            {
                return;
            }
            StockDom.SetCell(dom, row, name, (-Math.Abs(value)).ToString(CultureInfo.InvariantCulture), names);
        }
    }
}
