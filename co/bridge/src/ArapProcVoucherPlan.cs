using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 处理制单的计划（在 Prepare 的事务里）：拼分录、按 bYPzKMHB 合并、排序、凭证类别、档案核对，再交给 ArapVoucherSave 用的 VoucherPlan。
    // 另有预演的计划凭证（Dry）和成功响应（Body）。
    internal static class ArapProcVoucherPlan
    {
        public static void Make(object conn, ProcPlan plan, string date, int year)
        {
            ProcVoucherAsk ask = plan.Ask;
            VoucherPlan gl = plan.Gl;
            gl.Date = date;
            gl.Key.Year = year;
            gl.Key.Period = DateTime.ParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture).Month;
            List<ProcPart> parts = Parts(conn, plan, year);
            List<ProcLine> lines = ArapProcVoucherLines.Build(conn, ask, parts, year);
            if (MergeOn(conn, ask.Flag))
            {
                lines = ArapProcVoucherParts.Merge(lines);
            }
            lines = ask.Notes ? ArapProcVoucherNotes.Order(lines) : (ask.Bad ? ArapProcVoucherBad.Order(lines) : ArapProcVoucherParts.Order(lines));
            // 现金流量项目：票据处理、坏账收回（借银行）；其余处理不挂项目，给了 cash_items 一律 400（ArapCashItems.Finish）。
            if (ask.Notes || ask.Style == "9H")
            {
                ArapProcVoucherNotes.Flows(conn, lines, year, date, ask.CashItems);
            }
            else
            {
                ArapCashItems.Finish(conn, ask.CashItems);
            }
            gl.Key.Sign = Sign(conn, ArapProcVoucherReq.SignOf(ask));
            gl.Seq = GlState.SignSeq(conn, gl.Key.Sign);
            gl.OutSign = ArapProcVoucherReq.OutSign(ask.Style, ask.Flag);
            gl.Digest = lines[0].Line.Digest;
            ArapVoucherRefs.Check(conn, ArapProcVoucherLines.AsRows(lines));
            ArapProcVoucherParts.Fill(plan, lines);
            gl.Doc = Doc(plan);
            gl.Docs = new List<VoucherDoc>();
            gl.Docs.Add(gl.Doc);
        }

        // 票据处理（9A / 9D / 9E / 9C）的分录另由 ArapProcVoucherNotes 按 AP_Note_Sub 和往来明细拼；坏账处理由 ArapProcVoucherBad 拼。
        static List<ProcPart> Parts(object conn, ProcPlan plan, int year)
        {
            ProcVoucherAsk ask = plan.Ask;
            if (ask.Notes)
            {
                return ArapProcVoucherNotes.Parts(plan, ArapProcVoucherNotes.ExpenseAccount(conn, plan, year));
            }
            if (ask.Bad)
            {
                return ArapProcVoucherBad.Parts(conn, plan, year);
            }
            return ArapProcVoucherParts.Build(plan.Rows, ask.Style, ask.PlCode, ask.Digest);
        }

        // 给 ArapVoucherSave / ArapVoucherBack.Clash 用的「单据」：flag、第一行分录的来源（报文的 bill_type / bill_id 缺省值），
        // Rows 的行数 = 全部批次的明细行数（Clash 按它核对引用外部业务号的明细行数）。
        static VoucherDoc Doc(ProcPlan plan)
        {
            VoucherDoc doc = new VoucherDoc();
            doc.Flag = plan.Ask.Flag;
            doc.Detail = WriteoffSql.Detail(plan.Ask.Flag);
            doc.VType = plan.Sources[0][0];
            doc.Code = plan.Sources[0][1];
            foreach (ProcVoucherRow row in plan.Rows)
            {
                // 计提 9F 的批次行没有往来明细（Aid 为空），不计入引用外部业务号的明细行数。
                if (row.Aid.Length == 0)
                {
                    continue;
                }
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["aid"] = row.Aid;
                doc.Rows.Add(one);
            }
            return doc;
        }

        // 应收应付选项 bYPzKMHB（制单时同科目合并，缺省合并）。
        static bool MergeOn(object conn, string flag)
        {
            string value = Rows.Scalar(conn, "select cValue from AccInformation where cSysID=? and cName=?", new object[] { flag, "bYPzKMHB" });
            string text = value == null ? "" : value.Trim();
            return text.Length == 0 || text == "1" || text.Equals("true", StringComparison.OrdinalIgnoreCase);
        }

        static string Sign(object conn, string sign)
        {
            string adjust = Rows.Scalar(conn, "select convert(varchar(4), isnull(iAdjustFlag,0)) from dsign where csign=?", new object[] { sign });
            if (adjust == null || adjust.Trim() == "1")
            {
                throw ArapVoucherDoc.Refuse("凭证类别 " + sign + " 不存在或是调整期凭证类别，请用 sign 指定");
            }
            return sign;
        }

        // 预演（validate）：Prepare 提交之前把计划凭证交给 detail；提交钩子回滚（外部业务号的分配一并撤销），不调用凭证导入。
        public static void Dry(ProcPlan plan)
        {
            if (!DryRun.Active)
            {
                return;
            }
            VoucherPlan gl = plan.Gl;
            Dictionary<string, object> voucher = new Dictionary<string, object>();
            voucher["flag"] = plan.Ask.Flag;
            voucher["proc_style"] = plan.Ask.Style;
            voucher["out_sign"] = gl.OutSign;
            voucher["iyear"] = gl.Key.Year;
            voucher["iperiod"] = gl.Key.Period;
            voucher["csign"] = gl.Key.Sign;
            voucher["date"] = gl.Date;
            voucher["pz_id"] = gl.PzId;
            voucher["pz_id_note"] = "预演取的外部业务号已随回滚撤销，实际制单会重新取号";
            voucher["making_system"] = plan.Ask.Flag;
            List<object> lines = GlDryRun.Lines(gl.Draft);
            for (int i = 0; i < lines.Count && i < plan.Sources.Count; i++)
            {
                Dictionary<string, object> line = lines[i] as Dictionary<string, object>;
                if (line != null)
                {
                    line["bill_type"] = plan.Sources[i][0];
                    line["bill_code"] = plan.Sources[i][1];
                }
            }
            voucher["lines"] = lines;
            voucher["lines_total"] = gl.Draft.Lines.Count;
            voucher["batches"] = Batches(plan);
            DryRun.Set("voucher", voucher);
        }

        // 每个批次：批次号、明细行数（两张表合计）。
        static List<object> Batches(ProcPlan plan)
        {
            List<object> list = new List<object>();
            foreach (string no in plan.Ask.CancelNos)
            {
                int n = 0;
                foreach (ProcVoucherRow row in plan.Rows)
                {
                    n += row.CancelNo == no ? 1 : 0;
                }
                Dictionary<string, object> one = new Dictionary<string, object>();
                one["cancel_no"] = no;
                one["rows"] = n;
                list.Add(one);
            }
            return list;
        }

        // 响应：{ok, acc, flag, proc_style, out_sign, cancel_nos, rows, pz_id, making_system, voucher:{year, period, sign, no, num, date}, lines}。
        public static Dictionary<string, object> Body(WorkContext ctx, ProcPlan plan, List<Dictionary<string, object>> rows)
        {
            VoucherPlan gl = plan.Gl;
            Dictionary<string, object> voucher = new Dictionary<string, object>();
            voucher["year"] = gl.Key.Year;
            voucher["period"] = gl.Key.Period;
            voucher["sign"] = gl.Key.Sign;
            voucher["no"] = gl.Key.No;
            voucher["num"] = gl.PzNum();
            voucher["date"] = gl.Date;
            List<object> lines = new List<object>();
            foreach (Dictionary<string, object> row in rows)
            {
                lines.Add(Line(row));
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["acc"] = ctx.Item.Acc;
            body["flag"] = plan.Ask.Flag;
            body["proc_style"] = plan.Ask.Style;
            body["out_sign"] = gl.OutSign;
            body["cancel_nos"] = new List<object>(plan.Ask.CancelNos.ToArray());
            body["rows"] = plan.Rows.Count;
            body["pz_id"] = gl.PzId;
            body["making_system"] = gl.MakingSystem;
            body["voucher"] = voucher;
            body["lines"] = lines;
            return body;
        }

        // 一行分录（字段同 arap/voucher 的 lines）。
        static Dictionary<string, object> Line(Dictionary<string, object> row)
        {
            Dictionary<string, object> one = new Dictionary<string, object>();
            one["entry"] = CoRows.AsId(CoRows.Col(row, "inid"));
            one["account"] = CoRows.Col(row, "ccode");
            one["digest"] = CoRows.Col(row, "cdigest");
            one["debit"] = WriteoffSql.Num(CoRows.Col(row, "md"));
            one["credit"] = WriteoffSql.Num(CoRows.Col(row, "mc"));
            string[][] aux = new string[][]
            {
                new string[] { "dept", "cdept_id" }, new string[] { "person", "cperson_id" }, new string[] { "customer", "ccus_id" },
                new string[] { "supplier", "csup_id" }, new string[] { "item_class", "citem_class" }, new string[] { "item", "citem_id" },
                new string[] { "settle", "csettle" }, new string[] { "bill_code", "coutid" }
            };
            foreach (string[] pair in aux)
            {
                string value = CoRows.Col(row, pair[1]);
                one[pair[0]] = value.Length > 0 ? value : null;
            }
            return one;
        }
    }
}
