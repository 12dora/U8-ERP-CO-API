using System.Collections.Generic;

namespace U8Co
{
    // 币种（archive=currency）。新增走 EAI 分发表指向的 U8PzInsert.ICurrency（不设 ToEAICon，见 ArcGl）；
    // ICurrency 的 diffedit / delete 实测回「暂不提供此项功能」，修改、删除走受控 SQL（ArcCurrencySql）。
    // 编码是币种名称 cexch_name（最长 8，单据和凭证里都按名称引用），报文按 <name> 发；币种符号在 fields 的 code（cexch_code）。
    // 本位币（iotherused=-1）在账套参数里维护，一律 409。
    internal static class ArcCurrency
    {
        const string ProgId = "U8PzInsert.ICurrency";
        const string SymbolSql = "SELECT cexch_name FROM foreigncurrency WHERE cexch_code=?";

        public static ApiResult Write(WorkContext ctx, ArcReq req)
        {
            ArcGuard.Permit(ctx, req);
            if (req.Op != "create")
            {
                return ArcCurrencySql.Write(ctx, req);
            }
            object conn = ctx.Conn;
            if (ArcRead.Row(conn, req.Kind, req.Code) != null)
            {
                throw ArcGuard.State("档案编码已存在：" + req.Code);
            }
            string symbol = req.Fields.Get("code").Trim();
            string owner = Rows.Scalar(conn, SymbolSql, new object[] { symbol });
            if (owner != null)
            {
                throw ArcGuard.State("币种符号 " + symbol + " 已被币种 " + owner.Trim() + " 使用");
            }
            ArcGlCall call = new ArcGlCall();
            call.ProgId = ProgId;
            call.Proc = "add";
            call.Read = ArcGl.Plain(req.Kind, req.Code);
            IList<string> tags = req.Fields.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                call.Bag.Put(tags[i], req.Fields.Get(tags[i]));
            }
            string[] d = req.Kind.Defaults;
            for (int i = 0; i + 1 < d.Length; i += 2)
            {
                if (!call.Bag.Has(d[i]))
                {
                    call.Bag.Put(d[i], d[i + 1]);
                }
            }
            ApiResult done = ArcGl.Run(ctx, req, call);
            ArcCurrencySql.CheckCreated(ctx, req, call.Bag);
            return done;
        }
    }
}
