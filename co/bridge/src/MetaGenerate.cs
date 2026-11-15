using System;
using System.Collections.Generic;

namespace U8Co
{
    // vouchers/generate 按（目标类型, 来源类型）的可写字段，与 Dispatch.GenerateOf / GeneratePu 的分派一致。
    // 表体一律必带 source_line_id、quantity（Json.OneGenerate），这两个列在 line_control 里；销售出库的表体可以不带。
    internal static class MetaGenerate
    {
        internal static Dictionary<string, object> Of(string target, string source)
        {
            // 红字销售发票参照退货单，lines 可省（SaleGen.RedInvoice）。
            // 红冲蓝字销售发票同样（SaleGen.BlueRedInvoice）。
            if (target == "sale_invoice" && (source == SaleReturn.KindName || source == SaleGen.BlueSourceName))
            {
                return SaleGen.MetaRedInvoice();
            }
            Dictionary<string, object> spec = Sales(target);
            if (spec == null)
            {
                spec = Supply(target, source);
            }
            if (spec == null)
            {
                spec = Quality(target);
            }
            // 调拨单参照调拨申请单（StockGenTr）：表头不收仓库，表体只收 source_line_id、quantity、cbmemo。
            if (spec == null && target == "transfer")
            {
                spec = Make(StockGen.TrHeadKey, StockGen.TrLineKey, 200, None());
            }
            if (spec == null)
            {
                spec = MetaWritable.Spec(null, null, 0, 0, new string[0]);
                spec["required"] = MetaWritable.Required(new string[0], new string[0]);
            }
            return spec;
        }

        // SaleGen：发货单表体带自由项，销售发票不带；销售出库表头不收，表体可选按行部分生成（StockGen.SaleOutLines）。
        static Dictionary<string, object> Sales(string target)
        {
            if (target == "dispatch")
            {
                return Make(
                    delegate(string low) { return SaleGen.MetaDispatch(low, true); },
                    delegate(string low) { return SaleGen.MetaDispatch(low, false); }, 200, None());
            }
            if (target == "sale_invoice")
            {
                return Make(
                    delegate(string low) { return SaleGen.MetaInvoice(low, true); },
                    delegate(string low) { return SaleGen.MetaInvoice(low, false); }, 200, None());
            }
            // 退货单参照蓝字发货单（SaleGen.CheckReturn）：表体不带自由项。
            if (target == "sale_return")
            {
                return Make(
                    delegate(string low) { return SaleGen.MetaReturn(low, true); },
                    delegate(string low) { return SaleGen.MetaReturn(low, false); }, 200, None());
            }
            // 销售出库（StockGen.SaleOutLines）：表头不收；lines 可选（0..200 行，收 source_line_id、quantity，
            // 另收 cbatch、cposition，同一发货行可按批号 / 货位拆成多行），不带 lines 整张发货单生成。
            if (target == "sale_out")
            {
                Dictionary<string, object> spec = MetaWritable.Spec(
                    null, delegate(string low) { return low == "cbatch" || low == "cposition"; }, 0, 200, MetaWritable.Control());
                spec["required"] = MetaWritable.Required(new string[0], new string[0]);
                return spec;
            }
            return null;
        }

        // 采购入库（StockGen、StockGenQm）、采购发票（PuInvReq）、到货单（PuArr）、材料出库 / 产成品入库（MfgReq）。
        static Dictionary<string, object> Supply(string target, string source)
        {
            // 参照来料检验单的采购入库、材料出库、产成品入库表体另收货位 cposition（MfgReq.LineOf 的 pos）。
            Func<string, bool> posLine = MetaFields.RowCheck(delegate(Dictionary<string, object> row) { MfgReq.LineOf(row, true); });
            Func<string, bool> puLine = MetaFields.RowCheck(delegate(Dictionary<string, object> row) { PuInvReq.LineOf(row); });
            if (target == "purchase_in")
            {
                return PurchaseIn(source, posLine);
            }
            if (target == "purchase_invoice")
            {
                return Make(MetaFields.InList(PuInvReq.HeadKeyList()), puLine, 200, new string[] { "cpbvcode" });
            }
            // 采购结算单参照采购发票：表头只收 settle_date，没有表体（PuSettleReq）。
            if (target == PuSettleRead.KindName)
            {
                return PuSettleReq.Meta();
            }
            if (target == "arrival")
            {
                return Make(MetaFields.InList(PuArr.MetaHeadKeys()), puLine, 200, None());
            }
            // 采购退货单（PuRet）：两种来源的表头表体规则都同到货单。
            if (target == "purchase_return")
            {
                return Make(MetaFields.InList(PuArr.MetaHeadKeys()), puLine, 200, None());
            }
            if (target == "material_out" || target == "product_in")
            {
                // 产成品入库参照产品检验单最多 CheckLinesMax 行（合并检验一个来源一行），其余来源 1 行（MfgReq.MaxLines）。
                return Make(MfgReq.HeadKey, posLine, MfgReq.MaxLines(target, source), new string[] { "cwhcode", "crdcode" });
            }
            return null;
        }

        // 采购入库：参照来料检验单表体 1 行（另收货位）；参照到货单字段同参照采购订单，lines 可省（整单按剩余），
        // 仓库取表头或各行一致的 cwhcode（StockGenArr）；参照采购退货单的红字入库（StockGenRed）与参照采购订单同一套字段。
        static Dictionary<string, object> PurchaseIn(string source, Func<string, bool> posLine)
        {
            if (source == "qm_incoming_check")
            {
                return Make(StockGen.MetaHeadKey, posLine, 1, Warehouse());
            }
            if (source == StockGen.ArrSourceName)
            {
                Dictionary<string, object> arr = MetaWritable.Spec(StockGen.MetaHeadKey, StockGen.MetaLineKey, 0, 200,
                    MetaWritable.Control());
                arr["required"] = MetaWritable.Required(None(), MetaWritable.Control());
                return arr;
            }
            return Make(StockGen.MetaHeadKey, StockGen.MetaLineKey, 200, Warehouse());
        }

        // 质量单据（QmReq）：报检单表体 1 到 200 行，检验单恰好 1 行、表头必须有检验员；检验单表头 items 是数组
        // （每项 cchkitemcode、cchkguidecode、ccheckvalue、ctargetqjug），meta 里只列出字段名 items。
        static Dictionary<string, object> Quality(string target)
        {
            QmSpec spec = QmSpec.Find(target);
            // 不良品处理单（QmRejReq）：表体 1 到 200 行，每行必须有处理方式、不良原因。
            if (spec == null && QmRejSpec.Find(target) != null)
            {
                Dictionary<string, object> rej = MetaWritable.Spec(QmRejReq.HeadKey, QmRejReq.LineKey, 1, QmRejReq.LinesMax,
                    MetaWritable.Control());
                List<string> need = new List<string>(MetaWritable.Control());
                need.Add("cscrapdiscode");
                need.Add("creasoncode");
                rej["required"] = MetaWritable.Required(None(), need.ToArray());
                return rej;
            }
            // 其他检验单参照其他报检单：请求规则同来料检验单（QmReq，替身 QmOthSpec.CheckAsk）。
            if (spec == null && target == QmOthSpec.CheckKind)
            {
                spec = QmOthSpec.CheckAsk;
            }
            if (spec == null)
            {
                return null;
            }
            return Make(delegate(string low) { return QmReq.HeadKey(spec, low); },
                delegate(string low) { return QmReq.LineKey(spec, low); }, spec.Inspect ? 200 : 1,
                spec.Inspect ? None() : new string[] { "ccheckpersoncode" });
        }

        static Dictionary<string, object> Make(Func<string, bool> head, Func<string, bool> line, int linesMax, string[] required)
        {
            Dictionary<string, object> spec = MetaWritable.Spec(head, line, 1, linesMax, MetaWritable.Control());
            spec["required"] = MetaWritable.Required(required, MetaWritable.Control());
            return spec;
        }

        static string[] Warehouse()
        {
            return new string[] { "cwhcode" };
        }

        static string[] None()
        {
            return new string[0];
        }
    }
}
