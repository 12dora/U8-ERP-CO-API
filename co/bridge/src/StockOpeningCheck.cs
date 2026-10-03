using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 期初结存 EAI 导入之前的查库校验（StockOpeningAdd 调用，全部在 DryRun.Stop 之前）：数量小数位（AA 的 iStrsQuanDecDgt，
    // 同生产订单 MoCreateSql）、存货存在与批次管理、仓库存在与货位管理（货位核对复用 StockPurInPos.CheckCreate）。
    // 一律 400 带 field，免得 U8 只导入一部分行。
    internal static class StockOpeningCheck
    {
        const string DigitsSql = "select cValue from AccInformation where cSysID='AA' and cName='iStrsQuanDecDgt'";
        const string InvSql = "select convert(varchar(5), isnull(bInvBatch,0)) as batch from Inventory where cInvCode=?";
        // rdrecords34.iQuantity 存 6 位小数；账套设置读不到或超出时按 6。
        const int DigitsMax = 6;

        // 返回数量小数位（回读核对时按它舍入比较）。
        internal static int Run(object conn, List<OpeningEntry> entries)
        {
            int digits = Digits(conn);
            Dictionary<string, bool> batch = new Dictionary<string, bool>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < entries.Count; i++)
            {
                OpeningEntry e = entries[i];
                string at = FieldPath.Item("lines", e.Line);
                CheckDigits(e, digits, at);
                CheckBatch(conn, batch, e, at);
                CheckPosition(conn, e, at);
            }
            return digits;
        }

        static int Digits(object conn)
        {
            string text = Rows.Scalar(conn, DigitsSql, new object[0]);
            int n;
            if (text != null && int.TryParse(text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out n)
                && n >= 0 && n <= DigitsMax)
            {
                return n;
            }
            return DigitsMax;
        }

        // U8 界面按存货数量小数位限制录入，多出的小数不送给 U8。
        internal static void CheckDigits(OpeningEntry e, int digits, string at)
        {
            if (decimal.Round(e.Qty, digits) != e.Qty)
            {
                throw BridgeException.BadField(FieldPath.Join(at, "iquantity"),
                    "iquantity 最多 " + digits.ToString(CultureInfo.InvariantCulture) + " 位小数（U8 存货数量小数位）");
            }
        }

        // 存货存在；启用批次管理的必须填批号，未启用的不能填（用语同无来源其他出入库 SrcLessSt）。
        static void CheckBatch(object conn, Dictionary<string, bool> cache, OpeningEntry e, string at)
        {
            bool batch;
            if (!cache.TryGetValue(e.Inv, out batch))
            {
                Dictionary<string, object> row = Rows.One(conn, InvSql, new object[] { e.Inv });
                if (row == null)
                {
                    throw BridgeException.BadField(FieldPath.Join(at, "cinvcode"), "存货 " + e.Inv + " 不存在");
                }
                batch = CoRows.FlagOf(row, "batch");
                cache[e.Inv] = batch;
            }
            bool sent = e.Tags.ContainsKey("cbatch");
            if (batch && !sent)
            {
                throw BridgeException.BadField(FieldPath.Join(at, "cbatch"), "存货 " + e.Inv + " 启用批次管理，必须填批号 cbatch");
            }
            if (!batch && sent)
            {
                throw BridgeException.BadField(FieldPath.Join(at, "cbatch"), "存货 " + e.Inv + " 未启用批次管理，不能填批号");
            }
        }

        // 仓库存在；货位管理的仓库必须填末级货位，未启用的不能填（StockPurInPos 的规则和用语），错误补上 field。
        static void CheckPosition(object conn, OpeningEntry e, string at)
        {
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["cwhcode"] = e.Wh;
            Dictionary<string, object> line = new Dictionary<string, object>();
            string pos;
            if (e.Tags.TryGetValue("cposition", out pos))
            {
                line["cposition"] = pos;
            }
            try
            {
                StockPurInPos.CheckCreate(conn, head, new object[] { line });
            }
            catch (BridgeException ex)
            {
                if (ex.Status == 400 && string.IsNullOrEmpty(ex.Field))
                {
                    ex.WithField(FieldPath.Join(at, ex.Message.StartsWith("仓库不存在", StringComparison.Ordinal) ? "cwhcode" : "cposition"));
                }
                throw;
            }
        }
    }
}
