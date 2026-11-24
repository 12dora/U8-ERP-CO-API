using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的不良品处理单修改部分：接线（可修改、预演、功能 id）、请求校验（表体 op / line_id、禁收字段）、处理后数量、
    // 处理行的唯一键。不连库、不建 COM。
    internal static class QmRejEditSelfTest
    {
        public static void Run()
        {
            CheckWiring();
            CheckParse();
            CheckRefuse();
            CheckDim();
        }

        static void CheckWiring()
        {
            string[] kinds = new string[] { "qm_incoming_reject", "qm_product_reject" };
            string[] auths = new string[] { "QM02010303", "QM02020303" };
            for (int i = 0; i < kinds.Length; i++)
            {
                VoucherKind k = Kinds.Find(kinds[i]);
                True("rej edit kind " + kinds[i], k != null && k.Updatable && QmRejOps.Handles(k) && !QmEditReq.Handles(k));
                Expect("rej edit dry " + kinds[i], "validate", DryRunModes.Lookup("vouchers/update", kinds[i], "update", ""));
                PermRule rule = PermRegistry.ForKey("write:" + kinds[i] + ":update");
                True("rej edit rule " + kinds[i], rule != null && rule.Auths.Length == 1 && rule.Auths[0] == auths[i]);
            }
            // 合并检验：处理单带出所属合并明细（MergeId），检验单带出合并标记。
            True("rej merge id", QmRejOps.DocSql.Contains(QmRejSrc.MergeIdColumn));
            True("rej check merged", QmRejSrc.CheckSql(QmRejSpec.Find("qm_product_reject")).Contains("BMERGECHECKFLAG"));
        }

        static void CheckParse()
        {
            QmRejSpec spec = QmRejSpec.Find("qm_product_reject");
            Dictionary<string, object> head = new Dictionary<string, object>();
            head["chDefine15"] = "文本A";
            head["cDefine1"] = "DEMO-001";
            QmRejEditAsk ask = QmRejEditReq.Parse(spec, head, new object[]
            {
                Op("update", 11, "quantity", 1, "fDimQuantity", 1),
                Op("add", 0, "quantity", 1.5d, "cScrapDisCode", "Sys01", "creasoncode", "02"),
                Op("delete", 12)
            });
            Expect("rej edit head", "文本A|DEMO-001", ask.Head.Head["chdefine15"] + "|" + ask.Head.Head["cdefine1"]);
            Expect("rej edit ops", "update:11 add:0 delete:12", Ops(ask));
            QmRejEditOp up = ask.Ops[0];
            True("rej edit update sent", up.Qty == 1m && up.DimQty == 1m && up.Dispose == null && up.Reason == null && up.Wh == null);
            QmRejEditOp add = ask.Ops[1];
            True("rej edit add", add.Qty == 1.5m && add.Dispose == "Sys01" && add.Reason == "02" && add.DimInv == null);
            QmRejEditAsk headOnly = QmRejEditReq.Parse(spec, head, null);
            True("rej edit head only", headOnly.Ops.Count == 0 && headOnly.Head.Lines.Count == 0);
            True("rej edit line keys", QmRejEditReq.LineKey("fdimquantity") && !QmRejEditReq.LineKey("source_line_id"));
            Expect("rej edit key", QmRejEditLines.Key("01", "sys03"), QmRejEditLines.Key(" 01", "SYS03 "));
        }

        static void CheckRefuse()
        {
            QmRejSpec spec = QmRejSpec.Find("qm_incoming_reject");
            Field("rej edit empty", "head", delegate { QmRejEditReq.Parse(spec, null, null); });
            Field("rej edit checkid", "head.checkid", delegate { QmRejEditReq.Parse(spec, One("checkid", 5), null); });
            Field("rej edit date", "head.ddate", delegate { QmRejEditReq.Parse(spec, One("ddate", ""), null); });
            Lines("rej edit source", "lines.0.source_line_id", spec, Op("update", 11, "source_line_id", 5));
            Lines("rej edit inv", "lines.0.cinvcode", spec, Op("update", 11, "cinvcode", "A01"));
            Lines("rej edit op", "lines.0.op", spec, Op("modify", 11, "quantity", 1));
            Lines("rej edit add id", "lines.0.line_id", spec, Op("add", 11, "quantity", 1, "cscrapdiscode", "Sys01", "creasoncode", "01"));
            Lines("rej edit add reason", "lines.0.creasoncode", spec, Op("add", 0, "quantity", 1, "cscrapdiscode", "Sys01"));
            Lines("rej edit add qty", "lines.0.quantity", spec, Op("add", 0, "cscrapdiscode", "Sys01", "creasoncode", "01"));
            Lines("rej edit no id", "lines.0.line_id", spec, Op("update", 0, "quantity", 1));
            Lines("rej edit nothing", "lines.0.line_id", spec, Op("update", 11));
            Lines("rej edit delete extra", "lines.0.op", spec, Op("delete", 11, "quantity", 1));
            Lines("rej edit blank code", "lines.0.cscrapdiscode", spec, Op("update", 11, "cscrapdiscode", " "));
            Lines("rej edit dim zero", "lines.0.fdimquantity", spec, Op("update", 11, "fdimquantity", 0));
            Lines("rej edit dup", "lines.1.line_id", spec, Op("update", 11, "quantity", 1), Op("delete", 11));
        }

        // 降级行的处理后数量：DimQty 大于 0 时按它写 FDIMQUANTITY 与件数，否则同处理数量（生单）。
        static void CheckDim()
        {
            QmRejUnit unit = new QmRejUnit();
            QmRejLine line = new QmRejLine();
            line.Qty = 4m;
            line.Flow = QmRejSrc.DimFlow;
            line.DimInv = "B01";
            line.DimUnit = "U2";
            line.DimRate = 2m;
            line.DimQty = 3m;
            Dictionary<string, string> f = Map(QmRejDom.LineFields(line, unit));
            Expect("rej edit dim qty", "4 3 1.5", f["FQUANTITY"] + " " + f["FDIMQUANTITY"] + " " + f["FDIMNUM"]);
            True("rej edit no inv dep", !f.ContainsKey("CINVCODE") || f["CINVCODE"].Length == 0);
            line.DimQty = 0m;
            Expect("rej edit dim default", "4", Map(QmRejDom.LineFields(line, unit))["FDIMQUANTITY"]);
        }

        static Dictionary<string, object> Op(string op, int id, params object[] pairs)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["op"] = op;
            if (id > 0)
            {
                row["line_id"] = id;
            }
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                row[(string)pairs[i]] = pairs[i + 1];
            }
            return row;
        }

        static Dictionary<string, object> One(string key, object value)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row[key] = value;
            return row;
        }

        static string Ops(QmRejEditAsk ask)
        {
            List<string> parts = new List<string>();
            for (int i = 0; i < ask.Ops.Count; i++)
            {
                parts.Add(ask.Ops[i].Op + ":" + ask.Ops[i].LineId);
            }
            return string.Join(" ", parts.ToArray());
        }

        static Dictionary<string, string> Map(List<string[]> fields)
        {
            Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.Ordinal);
            for (int i = 0; i < fields.Count; i++)
            {
                map[fields[i][0]] = fields[i][1];
            }
            return map;
        }

        static void Lines(string name, string field, QmRejSpec spec, params object[] lines)
        {
            Field(name, field, delegate { QmRejEditReq.Parse(spec, null, lines); });
        }

        static void Field(string name, string field, Action act)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                if (ex.Status == 400 && ex.Field == field)
                {
                    return;
                }
                throw new InvalidOperationException(name + " " + ex.Field);
            }
            throw new InvalidOperationException(name);
        }

        static void True(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }

        static void Expect(string name, string want, string got)
        {
            if (!string.Equals(want, got, StringComparison.Ordinal))
            {
                throw new InvalidOperationException(name + ": " + got);
            }
        }
    }
}
