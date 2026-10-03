using System;
using System.Collections.Generic;

namespace U8Co
{
    // 单据各操作的可写字段。head / lines 为 null 表示该操作不收表头 / 表体。
    // 可写判断直接调用各领域类的白名单函数；required 与行数上下限照登录前校验（见各类注释）手写，改校验时同步这里。
    // 表头字段在发货单、销售发票生单时还须出现在 U8 模板的行集里，这一点只在运行时才知道。
    internal static class MetaWritable
    {
        static readonly string[] UpdateControl = new string[] { "op", "line_id" };
        // 物料清单修改按 sort_seq 定位行（BomReq）。
        static readonly string[] BomControl = new string[] { "op", "sort_seq" };
        static readonly string[] GenerateControl = new string[] { "source_line_id", "quantity" };
        // 新增走 StockDom 名单的类型；sale_out 是无来源新增（StockSaleOut），它的修改走 EditMore，不经 Rule。
        static readonly string[] StockDomKinds = new string[] { "other_in", "other_out", "transfer", "purchase_in", "sale_out" };

        internal static Dictionary<string, object> Of(VoucherKind k)
        {
            Dictionary<string, object> d = new Dictionary<string, object>();
            d["create"] = k.Creatable ? Create(k) : null;
            d["update"] = k.Updatable ? Update(k) : null;
            Dictionary<string, object> gen = new Dictionary<string, object>();
            if (k.Sources != null)
            {
                for (int i = 0; i < k.Sources.Length; i++)
                {
                    gen[k.Sources[i]] = MetaGenerate.Of(k.Name, k.Sources[i]);
                }
            }
            d["generate"] = gen;
            return d;
        }

        static Dictionary<string, object> Create(VoucherKind k)
        {
            // 采购手工结算：表头只收 settle_date，表体 1 到 400 行（PuSettleManReq）。
            if (PuSettleReq.Handles(k))
            {
                return PuSettleManReq.Meta();
            }
            Func<string, bool>[] rule = Rule(k);
            if (rule == null)
            {
                return null;
            }
            // 生产订单新增只收 1 到 50 行（MoCreateReq）。
            int max = k.Name == "production_order" ? MoCreateReq.LinesMax : 200;
            Dictionary<string, object> spec = Spec(rule[0], rule[1], 1, max, new string[0]);
            spec["required"] = Required(CreateHead(k), CreateLine(k));
            StockCo.RedMeta(k, spec);  // 无来源采购入库表头另收布尔 red（红字退库）
            return spec;
        }

        static Dictionary<string, object> Update(VoucherKind k)
        {
            if (k.Name == BomRoutes.KindName)
            {
                return BomUpdate();
            }
            if (k.Name == "production_order")
            {
                return MoUpdateMeta();
            }
            // 质量单据修改：只收表头（检验单的检验项目在 head.items），不收 lines（QmEditReq）。
            if (QmEditReq.Handles(k))
            {
                Dictionary<string, object> qm = Spec(delegate(string low) { return QmEditReq.HeadKey(k, low); }, null, 0, 0,
                    new string[0]);
                qm["required"] = Required(new string[0], new string[0]);
                return qm;
            }
            Func<string, bool>[] rule = EditMore.Handles(k) ? UpdateRule(k) : Rule(k);
            if (rule == null)
            {
                return null;
            }
            Dictionary<string, object> spec = Spec(rule[0], rule[1], 0, 200, UpdateControl);
            spec["required"] = Required(new string[0], new string[0]);
            return spec;
        }

        // 物料清单修改：表头只收 version_desc、eff_date、parent_scrap；行同新增（update 不能改 inv_code、sort_seq 是定位键）。
        static Dictionary<string, object> BomUpdate()
        {
            Dictionary<string, object> spec = Spec(MetaFields.InList(BomReq.EditHeadNames()),
                MetaFields.InList(BomReq.LineNames()), 0, BomReq.LinesMax, BomControl);
            spec["required"] = Required(new string[0], new string[0]);
            return spec;
        }

        // 生产订单修改：表头只收 remark；行按 line_id 定位（op 只能 update），最多 50 行（MoUpdateReq）。
        static Dictionary<string, object> MoUpdateMeta()
        {
            Dictionary<string, object> spec = Spec(MetaFields.InList(MoUpdateReq.HeadNames()),
                MetaFields.InList(MoUpdateReq.LineNames()), 0, MoCreateReq.LinesMax, UpdateControl);
            spec["required"] = Required(new string[0], new string[0]);
            return spec;
        }

        // 生单来的单据和应收应付的修改名单与新增不同，由各领域类给出（EditMore.UpdateAllowed）。
        static Func<string, bool>[] UpdateRule(VoucherKind k)
        {
            return Pair(
                delegate(string low) { return EditMore.UpdateAllowed(k, true, low); },
                delegate(string low) { return EditMore.UpdateAllowed(k, false, low); });
        }

        // 与 Dispatch.CreateOf / Update 的分派一致。
        static Func<string, bool>[] Rule(VoucherKind k)
        {
            if (k.Name == "sale_order")
            {
                return Pair(
                    delegate(string low) { return SoFields.Allowed(low, true); },
                    delegate(string low) { return SoFields.Allowed(low, false); });
            }
            if (k.Name == "purchase_order")
            {
                return Pair(
                    delegate(string low) { return PuFields.Allowed(low, true); },
                    delegate(string low) { return PuFields.Allowed(low, false); });
            }
            if (Array.IndexOf(StockDomKinds, k.Name) >= 0)
            {
                return Pair(
                    delegate(string low) { return StockDom.MetaAllowed(k, true, low); },
                    delegate(string low) { return StockDom.MetaAllowed(k, false, low); });
            }
            if (k.Family == "ar")
            {
                return Arap(ArapReq.Spec(k).Close);
            }
            Func<string, bool>[] mfg = MfgRule(k);
            if (mfg != null)
            {
                return mfg;
            }
            // 请购单：PuAppFields.Allowed。
            if (k.Name == PuAppRoutes.KindName)
            {
                return Pair(
                    delegate(string low) { return PuAppFields.Allowed(low, true); },
                    delegate(string low) { return PuAppFields.Allowed(low, false); });
            }
            // 无来源新增（发货单、先开票销售发票、到货单、材料出库单）：SrcLessReq 的名单。
            return SrcLessReq.MetaRule(k);
        }

        // 生产订单新增：MoCreateReq 的表头、表体名单（修改另见 MoUpdateMeta）；物料清单新增：BomReq 的名单。
        static Func<string, bool>[] MfgRule(VoucherKind k)
        {
            // 形态转换单、调拨申请单、盘点单（StockMiscDom）和期初结存（StockOpening，名单为 EAI storeqc 有标签的字段，StockOpeningEai）也在这里接上，免得 Rule 的分支再加。
            if (StockMisc.Handles(k) || StockOpening.Handles(k) || PositionAdjust.Handles(k))
            {
                return Pair(
                    delegate(string low) { return StockDom.MetaAllowed(k, true, low); },
                    delegate(string low) { return StockDom.MetaAllowed(k, false, low); });
            }
            if (k.Name == "production_order")
            {
                return Pair(MetaFields.InList(MoCreateReq.HeadNames()), MetaFields.InList(MoCreateReq.LineNames()));
            }
            if (k.Name == BomRoutes.KindName)
            {
                return Pair(MetaFields.InList(BomReq.CreateHeadNames()), MetaFields.InList(BomReq.LineNames()));
            }
            // 其他报检单无来源新增（QmOthReq）。
            if (QmOthSpec.IsInspect(k))
            {
                return Pair(QmOthReq.HeadKey, QmOthReq.LineKey);
            }
            return null;
        }

        // 区间照 ArapReq.CheckCreate：表头 cdefine1–16，表体 cdefine22–37。
        static Func<string, bool>[] Arap(bool close)
        {
            HashSet<string> head = new HashSet<string>(MetaFields.Csv(close ? ArapReq.CloseHead : ArapReq.VouchHead));
            HashSet<string> line = new HashSet<string>(MetaFields.Csv(close ? ArapReq.CloseLine : ArapReq.VouchLine));
            return Pair(
                delegate(string low) { return head.Contains(low) || ArapReq.Span(low, "cdefine", 1, 16); },
                delegate(string low) { return line.Contains(low) || ArapReq.Span(low, "cdefine", 22, 37); });
        }

        // PuFields.RequireCreate、StockDomXfer、ArapReq.HeadBasics。
        static string[] CreateHead(VoucherKind k)
        {
            // 无来源新增：SrcLessReq.RequiredHead。
            if (SrcLessReq.Handles(k))
            {
                return SrcLessReq.RequiredHead(k);
            }
            if (k.Name == "purchase_order")
            {
                return new string[] { "cvencode", "cdepcode" };
            }
            if (k.Name == "transfer")
            {
                return new string[] { "cowhcode", "ciwhcode" };
            }
            string[] stock = StockHead(k);
            if (stock != null)
            {
                return stock;
            }
            if (k.Name == BomRoutes.KindName)
            {
                return (string[])BomReq.RequiredHead.Clone();
            }
            if (StockMisc.Handles(k))
            {
                return StockMisc.RequiredHead(k);
            }
            if (PositionAdjust.Handles(k))
            {
                return new string[] { "cwhcode" };
            }
            if (k.Family != "ar")
            {
                return new string[0];
            }
            if (ArapReq.Spec(k).Close)
            {
                return new string[] { "cdwcode", "ccode", "csscode" };
            }
            return new string[] { "cdwcode", "ccode" };
        }

        // 无来源采购入库单：StockPurIn.CheckHead；无来源销售出库单：StockSaleOut.Check。
        static string[] StockHead(VoucherKind k)
        {
            if (k.Name == "purchase_in")
            {
                return new string[] { "cwhcode", "cvencode" };
            }
            return k.Name == "sale_out" ? StockSaleOut.RequiredHead() : null;
        }

        // PuFields.RequireCreate；采购入库单 StockPurIn.CheckLine。
        static string[] CreateLine(VoucherKind k)
        {
            if (SrcLessReq.Handles(k))
            {
                return SrcLessReq.RequiredLine(k);
            }
            if (k.Name == "purchase_order")
            {
                return new string[] { "cinvcode" };
            }
            if (k.Name == "purchase_in" || k.Name == "sale_out")
            {
                return new string[] { "cinvcode", "iquantity" };
            }
            if (k.Name == "production_order")
            {
                return (string[])MoCreateReq.RequiredLine.Clone();
            }
            if (k.Name == BomRoutes.KindName)
            {
                return (string[])BomReq.RequiredLine.Clone();
            }
            if (StockMisc.Handles(k))
            {
                return StockMisc.RequiredLine(k);
            }
            if (PositionAdjust.Handles(k))
            {
                return new string[] { "cinvcode", "cbposcode", "caposcode", "iquantity" };
            }
            // 请购单：PuAppFields.CheckLine。
            if (k.Name == PuAppRoutes.KindName)
            {
                return (string[])PuAppFields.RequiredLines.Clone();
            }
            // 其他报检单：cinvcode、quantity（QmOthReq）；其余类型没有。
            return QmOthReq.CreateRequired(k);
        }

        internal static Func<string, bool>[] Pair(Func<string, bool> head, Func<string, bool> line)
        {
            return new Func<string, bool>[] { head, line };
        }

        internal static Dictionary<string, object> Spec(
            Func<string, bool> head, Func<string, bool> line, int linesMin, int linesMax, string[] control)
        {
            Dictionary<string, object> spec = new Dictionary<string, object>();
            spec["head"] = head == null ? null : MetaFields.Side(head);
            spec["lines"] = line == null ? null : MetaFields.Side(line);
            spec["lines_min"] = linesMin;
            spec["lines_max"] = linesMax;
            spec["line_control"] = (string[])control.Clone();
            return spec;
        }

        internal static Dictionary<string, object> Required(string[] head, string[] lines)
        {
            Dictionary<string, object> req = new Dictionary<string, object>();
            req["head"] = head;
            req["lines"] = lines;
            return req;
        }

        internal static string[] Control()
        {
            return (string[])GenerateControl.Clone();
        }
    }
}
