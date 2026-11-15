using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 回读找到的新卡片。
    internal sealed class FaCardFound
    {
        public string CardNum;
        public int CardId;
    }

    // 撤销新增闸门的查库结果（FaCardSql.UndoRefusal 的输入，文本照库里原样，NULL 为 null）。
    internal sealed class FaUndoFacts
    {
        public int Versions;
        public string Input;
        public string Disposed;
        public string Changes;
        public string OptType;
        public string Voucher;
        public string Depr;
        public string SameAsset;
    }

    // 固定资产卡片写入的查库部分（ArcFaWrite）：调用 EAI 之前的档案核对、撤销新增的闸门、调用后在新连接上的回读。
    // 表名列名都是常量，调用方的值只进 ? 参数。
    internal static class FaCardSql
    {
        const string AssetSql = "SELECT TOP 1 sCardNum AS c FROM fa_Cards WHERE sAssetNum=?";
        const string BaseSql = "SELECT TOP 1 cexch_name AS c FROM foreigncurrency WHERE iotherused=-1";
        const string DeptSql = "SELECT TOP 1 CONVERT(varchar(1), ISNULL(bDepEnd, 0)) AS e FROM Department WHERE cDepCode=?";
        const string CardSql = "SELECT TOP 3 sCardID AS id, sAssetNum AS asset, CONVERT(varchar(10), dInputDate, 23) AS input,"
            + " CONVERT(varchar(10), dDisposeDate, 23) AS disposed, CONVERT(varchar(10), iOptType) AS opt, sZWVoucherNum AS serial"
            + " FROM fa_Cards WHERE sCardNum=? ORDER BY sCardID";
        const string ChangesSql = "SELECT COUNT(*) FROM fa_Vouchers WHERE sCardNum=?";
        // 卡片是否已制单：固定资产的凭证行（fa_ZWVouchers 待传、GL_accvouch 已传总账）用 coutid 记业务流水号，
        // 即卡片的 sZWVoucherNum 左补零到 20 位（实测：sZWVoucherNum 每张卡片都有，不是凭证号）。
        const string VoucheredSql = "SELECT (SELECT COUNT(*) FROM fa_ZWVouchers WHERE coutsysname=N'FA' AND coutid=?)"
            + " + (SELECT COUNT(*) FROM GL_accvouch WHERE coutsysname=N'FA' AND coutid=? AND iflag IS NULL)";
        // 同一资产编号下的卡片张数：U8 导入按资产编号删除，不唯一时可能删到别的卡片。
        const string SharedSql = "SELECT COUNT(DISTINCT sCardNum) FROM fa_Cards WHERE sAssetNum=?";
        // 固定资产当前期间已计提折旧（U8 计提后不能删除本期卡片，删了会留下孤立的折旧和凭证数据）。
        const string DeprSql = "SELECT COUNT(*) FROM fa_DeprList WHERE iyear=? AND iPeriod=?";
        const string NewSql = "SELECT TOP 3 sCardID AS id, sCardNum AS c, sAssetName AS n, sTypeNum AS t, CONVERT(varchar(40), dblValue) AS v"
            + " FROM fa_Cards WHERE sAssetNum=? ORDER BY sCardID";
        const string GoneSql = "SELECT COUNT(*) FROM fa_Cards WHERE sCardNum=?";

        // 档案表（编码列 sID / sNum）：{ 表, 编码列, 标签, 字段名, 是否要求末级 }。模板要求类别、增加方式、使用状况是末级，
        // 末级按编码前缀判断（编码分级，下级以上级编码开头）；折旧方法不分级，只查存在。
        static readonly string[][] Refs = new string[][]
        {
            new string[] { "fa_AssetTypes", "sNum", "资产类别", "type_code", "1" },
            new string[] { "fa_Origins", "sID", "增加方式", "origin_code", "1" },
            new string[] { "fa_Status", "sID", "使用状况", "status_code", "1" },
            new string[] { "fa_Depreciations", "sID", "折旧方法", "depreciation_method_code", "" }
        };

        // 新增前：资产编号未用、四类固定资产档案存在且为末级、部门存在且为末级、币种是本位币；返回本位币名称。
        internal static string CheckAdd(object conn, FaCardPlan plan)
        {
            string card = Rows.Scalar(conn, AssetSql, new object[] { plan.AssetNum });
            if (card != null)
            {
                throw ArcGuard.State("资产编号 " + plan.AssetNum + " 已被卡片 " + card.Trim() + " 使用");
            }
            string[] codes = new string[] { plan.Type, plan.Origin, plan.Status, plan.Method };
            for (int i = 0; i < Refs.Length; i++)
            {
                Leaf(conn, Refs[i], codes[i]);
            }
            string end = Rows.Scalar(conn, DeptSql, new object[] { plan.Dept });
            if (end == null)
            {
                throw ArcReq.Bad("部门 " + plan.Dept + " 不存在", "fields.dept_code");
            }
            if (end.Trim() != "1")
            {
                throw ArcReq.Bad("部门 " + plan.Dept + " 不是末级部门", "fields.dept_code");
            }
            return Currency(conn, plan.Currency);
        }

        static void Leaf(object conn, string[] spec, string code)
        {
            string at = FieldPath.Join("fields", spec[3]);
            string head = "SELECT TOP 1 " + spec[1] + " AS c FROM " + spec[0] + " WHERE ";
            if (Rows.Scalar(conn, head + spec[1] + "=?", new object[] { code }) == null)
            {
                throw ArcReq.Bad(spec[2] + " " + code + " 不存在", at);
            }
            if (spec[4] != "1")
            {
                return;
            }
            string child = Rows.Scalar(conn, head + spec[1] + " LIKE ? ESCAPE '\\' AND " + spec[1] + "<>?",
                new object[] { ArcRead.Like(code, false), code });
            if (child != null)
            {
                throw ArcReq.Bad(spec[2] + " " + code + " 不是末级", at);
            }
        }

        // 只收本位币：没给取本位币，给了必须等于本位币名称。
        static string Currency(object conn, string given)
        {
            string local = Rows.Scalar(conn, BaseSql, new object[0]);
            if (local == null || local.Trim().Length == 0)
            {
                throw ArcGuard.State("读不到账套的本位币");
            }
            local = local.Trim();
            if (given != null && !string.Equals(given, local, StringComparison.Ordinal))
            {
                throw ArcReq.Bad("固定资产卡片只支持本位币（" + local + "）", "fields.currency");
            }
            return local;
        }

        // 撤销新增的闸门：原始卡片或新增方式录入（iOptType 1 / 2；EAI 导入的是原始卡片 1，实测）、卡片只有一个版本、没有减少、没有变动单、没有制单、在固定资产当前期间录入，
        // 当前期间还没计提折旧，资产编号只属于这一张卡片；返回资产编号。
        internal static string CheckUndo(WorkContext ctx, string cardNum, FaPeriod period)
        {
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, CardSql, new object[] { cardNum }, 3);
            if (rows.Count == 0)
            {
                throw new BridgeException(404, "not_found", "档案不存在");
            }
            Dept(ctx, cardNum);
            Dictionary<string, object> row = rows[0];
            FaUndoFacts f = new FaUndoFacts();
            f.Versions = rows.Count;
            f.Input = ArcRead.Cell(row, "input");
            f.Disposed = ArcRead.Cell(row, "disposed");
            f.Changes = Rows.Scalar(ctx.Conn, ChangesSql, new object[] { cardNum });
            f.OptType = ArcRead.Cell(row, "opt");
            string serial = Text(row, "serial");
            if (serial.Length > 0)
            {
                string padded = serial.PadLeft(20, '0');
                f.Voucher = Rows.Scalar(ctx.Conn, VoucheredSql, new object[] { padded, padded });
            }
            f.Depr = Rows.Scalar(ctx.Conn, DeprSql, new object[] { period.Year, period.Period });
            string asset = Text(row, "asset");
            if (asset.Length > 0)
            {
                f.SameAsset = Rows.Scalar(ctx.Conn, SharedSql, new object[] { asset });
            }
            string refusal = UndoRefusal(f, period);
            if (refusal != null)
            {
                throw ArcGuard.State("卡片 " + cardNum + refusal);
            }
            return asset;
        }

        // 纯判断（自检覆盖）：先看卡片自身（减少、录入方式、版本、制单、录入期间），再看期间和资产编号。
        internal static string UndoRefusal(FaUndoFacts f, FaPeriod period)
        {
            string card = CardRefusal(f, period);
            if (card != null)
            {
                return card;
            }
            if (Count(f.Depr) > 0)
            {
                return " 所在的固定资产当前期间（" + period.Label() + "）已计提折旧，不能撤销新增；请先在 U8 客户端取消本期折旧";
            }
            if (Count(f.SameAsset) > 1)
            {
                return " 的资产编号同时被其他卡片使用，U8 按资产编号删除可能删错卡片，请在 U8 客户端处理";
            }
            return null;
        }

        static string CardRefusal(FaUndoFacts f, FaPeriod period)
        {
            if (f.Disposed != null)
            {
                return " 已减少，不能撤销新增";
            }
            if (f.OptType == null || (f.OptType.Trim() != "1" && f.OptType.Trim() != "2"))
            {
                return " 不是新增录入的卡片，不能撤销新增";
            }
            if (f.Versions > 1 || Count(f.Changes) > 0)
            {
                return " 已有变动单，不能撤销新增";
            }
            if (Count(f.Voucher) > 0)
            {
                return " 已制单，不能撤销新增；请先在 U8 客户端删除凭证";
            }
            if (!InPeriod(f.Input, period))
            {
                return " 不是固定资产当前期间（" + period.Label() + "）录入的，只能撤销本期新增的卡片";
            }
            return null;
        }

        static bool InPeriod(string input, FaPeriod period)
        {
            return input != null && string.CompareOrdinal(input, period.First) >= 0 && string.CompareOrdinal(input, period.Last) <= 0;
        }

        // 计数文本转整数；读不到按 0。
        static int Count(string text)
        {
            int n;
            return text != null && int.TryParse(text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out n) ? n : 0;
        }

        // 部门数据权限：卡片全部使用部门都在授权内（同读取的 ReportsFaPerm 口径），否则 403。
        internal static void Dept(WorkContext ctx, string cardNum)
        {
            List<object> ps = new List<object>();
            string sql = ReportsFaPerm.CardProbe(PermCheck.Of(ctx), cardNum, ps);
            if (sql != null && Rows.One(ctx.Conn, sql, ps.ToArray()) == null)
            {
                throw new BridgeException(403, "no_permission", PermCheck.DeniedRow);
            }
        }

        // 新增后的回读：按资产编号恰好一张，名称、类别、原值与请求一致。
        internal static bool Created(object conn, FaCardPlan plan, FaCardFound found)
        {
            List<Dictionary<string, object>> rows = Rows.Query(conn, NewSql, new object[] { plan.AssetNum }, 3);
            if (rows.Count != 1)
            {
                return false;
            }
            Dictionary<string, object> row = rows[0];
            if (!Same(row, plan))
            {
                return false;
            }
            found.CardNum = Text(row, "c");
            int.TryParse(Text(row, "id"), NumberStyles.Integer, CultureInfo.InvariantCulture, out found.CardId);
            return found.CardNum.Length > 0;
        }

        static bool Same(Dictionary<string, object> row, FaCardPlan plan)
        {
            decimal value;
            return string.Equals(Text(row, "n"), plan.Name, StringComparison.Ordinal)
                && string.Equals(Text(row, "t"), plan.Type, StringComparison.Ordinal)
                && decimal.TryParse(Text(row, "v"), NumberStyles.Float, CultureInfo.InvariantCulture, out value)
                && Math.Abs(value - plan.Value) < 0.005m;
        }

        // 单元格去空格，NULL 为空串。
        internal static string Text(Dictionary<string, object> row, string col)
        {
            return (ArcRead.Cell(row, col) ?? "").Trim();
        }

        internal static bool Gone(object conn, string cardNum)
        {
            return (Rows.Scalar(conn, GoneSql, new object[] { cardNum }) ?? "").Trim() == "0";
        }

        // 预演 detail：要导入的卡片（不含 XML）。
        internal static Dictionary<string, object> Preview(FaCardPlan plan, string currency)
        {
            Dictionary<string, object> d = new Dictionary<string, object>();
            d["asset_num"] = plan.AssetNum;
            d["name"] = plan.Name;
            d["type_code"] = plan.Type;
            d["original_value"] = plan.Value;
            d["dept_code"] = plan.Dept;
            d["currency"] = currency;
            return d;
        }
    }
}
