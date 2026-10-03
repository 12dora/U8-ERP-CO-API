using System;

namespace U8Co
{
    // 质量单据取号。实测：QM03 的 CO 照存送来的 CCHECKCODE（占位值也存），必须按 U8 编号规则取号；QM01 在「不允许手工改号」
    // （VoucherNumber.bAllowHandWork=0）时由 U8 用自己的流水号覆盖送来的值（保存前又不能为空）。所以报检单只在允许手工改号时
    // 才取号，否则送占位值、以回读的单号为准（QmGenInspect）。取号走 USERPCO 的登录对象 + BillNo（与库存单据相同）。
    internal static class QmNo
    {
        public static bool HandAllowed(object conn, string card)
        {
            return Values.Flag(QmSql.Scalar(conn, QmSql.HandNoSql, card));
        }

        // 按表头（种子取填好的字段，如单据日期）取号并写进表头的单号列，返回单号。
        public static string Allocate(WorkContext ctx, string card, object headDom, string field)
        {
            object st = StockCall.OpenCo(ctx);
            object usLogin = null;
            try
            {
                usLogin = ComUtil.Get(st, "Login");
                string code = BillNo.Allocate(usLogin, ctx.Session.Login, card, QmDom.HeadSeeds(headDom));
                CoRows.Note(ctx.Item, "code=" + code);
                using (QmRow r = QmRow.First(headDom))
                {
                    r.Raw(field, code);
                }
                return code;
            }
            finally
            {
                ComUtil.Final(usLogin);
                ComUtil.Final(st);
            }
        }

        // 报检单的单号占位：U8 保存时换成自己的流水号，响应的单号以回读为准。
        public static string Placeholder()
        {
            return "T" + DateTime.Now.ToString("yyMMddHHmmssfff", System.Globalization.CultureInfo.InvariantCulture);
        }
    }
}
