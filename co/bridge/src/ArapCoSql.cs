using System.Collections.Generic;

namespace U8Co
{
    // SQL 读出的表头快照。Link 只有应收/应付单有（cLink，也是表体外键）。
    internal sealed class ArapDoc
    {
        public int Id;
        public string Code;
        public string Link;
        public Dictionary<string, object> Row;

        public string Col(string name)
        {
            return CoRows.Col(Row, name);
        }

        public bool Flag(string name)
        {
            return CoRows.FlagOf(Row, name);
        }
    }

    // 表名、列名来自 VoucherKind 与 ArapSpec 常量；调用方的值只进参数。
    internal static class ArapSql
    {
        // cFlag / cVouchType 与类型不符按不存在处理（AP48 不是付款单、AR49 不是收款单，各归 ap_refund / ar_refund）。
        public static ArapDoc Head(object conn, VoucherKind kind, ArapSpec spec, int id)
        {
            Dictionary<string, object> row = Rows.One(conn, HeadSql(kind, spec), new object[] { id });
            if (row == null)
            {
                return null;
            }
            if (CoRows.Col(row, "flag") != spec.Flag || CoRows.Col(row, "vtype") != spec.VouchType)
            {
                return null;
            }
            ArapDoc doc = new ArapDoc();
            doc.Id = id;
            doc.Row = row;
            doc.Code = CoRows.Col(row, "code");
            doc.Link = CoRows.Col(row, "link");
            return doc;
        }

        public static ArapDoc Fresh(WorkContext ctx, VoucherKind kind, ArapSpec spec, int id)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                return Head(conn, kind, spec, id);
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static string HeadSql(VoucherKind kind, ArapSpec spec)
        {
            string fk = CoRows.Ident(kind.BodyFk);
            string sql = "select h." + CoRows.Ident(kind.IdColumn) + " as id, h." + CoRows.Ident(kind.CodeColumn)
                + " as code, h.cFlag as flag, h.cVouchType as vtype, h." + CoRows.Ident(kind.VerifierColumn)
                + " as verifier, h." + CoRows.Ident(kind.VerifyDateColumn) + " as verify_date, "
                + "h.dverifysystime as verify_sys, h.IsWfControlled as wf, h.cCoVouchType as co_type, "
                + "h.bStartFlag as start_flag, h.iAmount as amount, h.iAmount_f as amount_f, "
                + "h.iRAmount as ramount, h.iRAmount_f as ramount_f, "
                + "case when isnull(h.iRAmount,0)<>isnull(h.iAmount,0) or isnull(h.iRAmount_f,0)<>isnull(h.iAmount_f,0) "
                + "then 1 else 0 end as settled, (select count(*) from " + CoRows.Ident(kind.BodyTable) + " b where b."
                + fk + "=h." + fk + ") as lines, " + FamilySql(kind, spec, fk);
            return sql + " from " + CoRows.Ident(kind.HeadTable) + " h where h." + CoRows.Ident(kind.IdColumn) + "=?";
        }

        // 收付款单才有核销人、票据号、来源标志、网银标志和表体余额；应收/应付单带 cLink。
        static string FamilySql(VoucherKind kind, ArapSpec spec, string fk)
        {
            if (!spec.Close)
            {
                return "h.cPZid as voucher, h.cLink as link, 0 as line_settled";
            }
            return "h.cPzID as voucher, h.cCancelMan as settler, h.cNoteNo as note_no, h.cSrcFlag as src_flag, "
                + "h.cCoVouchID as co_id, h.bFromBank as from_bank, h.bToBank as to_bank, (select count(*) from "
                + CoRows.Ident(kind.BodyTable) + " b where b." + fk + "=h." + fk
                + " and (isnull(b.iRAmt,0)<>isnull(b.iAmt,0) or isnull(b.iRAmt_f,0)<>isnull(b.iAmt_f,0))) as line_settled";
        }

        public static int IdByCode(object conn, VoucherKind kind, ArapSpec spec, string code)
        {
            string sql = "select " + CoRows.Ident(kind.IdColumn) + " as id from " + CoRows.Ident(kind.HeadTable)
                + " where cFlag=? and cVouchType=? and " + CoRows.Ident(kind.CodeColumn) + "=?";
            List<Dictionary<string, object>> rows = Rows.Query(conn, sql, new object[] { spec.Flag, spec.VouchType, code }, 2);
            if (rows.Count != 1)
            {
                return 0;
            }
            return CoRows.AsId(CoRows.Col(rows[0], "id"));
        }

        // 删除后的回读：表头和表体都不应再有。
        public static bool Left(object conn, VoucherKind kind, ArapSpec spec, ArapDoc doc)
        {
            string head = "select top 1 convert(varchar(20), " + CoRows.Ident(kind.IdColumn) + ") from "
                + CoRows.Ident(kind.HeadTable) + " where " + CoRows.Ident(kind.IdColumn) + "=?";
            if (Rows.Scalar(conn, head, new object[] { doc.Id }) != null)
            {
                return true;
            }
            string body = "select top 1 convert(varchar(20), count(*)) from " + CoRows.Ident(kind.BodyTable)
                + " where " + CoRows.Ident(kind.BodyFk) + "=?";
            object key = spec.Close ? (object)doc.Id : doc.Link;
            return Rows.Scalar(conn, body, new object[] { key }) != "0";
        }

        // 弃审前：未制单、未核销、手工单据（或票据登记生成且票据未处理的收款单）、审核所在期间未结账。
        public static void RefuseUndo(object conn, ArapSpec spec, ArapDoc doc)
        {
            if (NoteReceipt(spec, doc))
            {
                RefuseNoteUndo(conn, spec, doc);
            }
            else
            {
                RefuseCommon(spec, doc, "不能弃审");
            }
            if (Processed(conn, spec, doc, true))
            {
                throw new BridgeException(409, "state_mismatch", "单据已核销或做过后续处理，不能弃审");
            }
            if (PeriodClosed(conn, spec, doc))
            {
                throw new BridgeException(409, "state_mismatch", "审核所在期间已结账，不能弃审");
            }
        }

        // 审核、弃审前：票据退回等处理生成的应收单、应付单（cCoVouchType 非空，如退回 9C 的 50）由处理本身维护审核状态，
        // 只能经取消处理（arap/process/cancel）撤销。
        public static void RefuseVerify(ArapSpec spec, ArapDoc doc)
        {
            if (!spec.Close && doc.Col("co_type").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据由票据退回等处理生成，不能审核或弃审");
            }
        }

        // 删除前：未审核（调用方已查）、手工单据、没有任何往来明细行。
        public static void RefuseDelete(object conn, ArapSpec spec, ArapDoc doc)
        {
            RefuseCommon(spec, doc, "不能删除");
            if (Processed(conn, spec, doc, false))
            {
                throw new BridgeException(409, "state_mismatch", "单据已有往来明细，不能删除");
            }
        }

        // 修改前（ArapEdit）：与删除同一套闸门；保存后的核对也再过一遍。
        public static void RefuseEdit(object conn, ArapSpec spec, ArapDoc doc)
        {
            RefuseCommon(spec, doc, "不能修改");
            if (Processed(conn, spec, doc, false))
            {
                throw new BridgeException(409, "state_mismatch", "单据已有往来明细，不能修改");
            }
        }

        static void RefuseCommon(ArapSpec spec, ArapDoc doc, string tail)
        {
            if (doc.Col("voucher").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已生成凭证，" + tail);
            }
            if (doc.Col("co_type").Length > 0 || doc.Flag("start_flag"))
            {
                throw new BridgeException(409, "state_mismatch", "只能处理手工录入的单据，" + tail);
            }
            if (spec.Close && FromElsewhere(doc))
            {
                throw new BridgeException(409, "state_mismatch", "单据来自票据或网银，" + tail);
            }
            if (Settled(doc))
            {
                throw new BridgeException(409, "state_mismatch", "单据已核销，" + tail);
            }
        }

        // 票据登记生成的收款单 / 付款单（NotesRegSql.MarkSql：cSrcFlag='C'、cCoVouchType='50'；应付票据）。同 U8 收付款单审核界面，
        // 票据未做任何处理时可以弃审；删除、修改仍拒绝（由 notes/delete 处理）。
        internal static bool NoteReceipt(ArapSpec spec, ArapDoc doc)
        {
            bool side = (spec.Flag == "AR" && spec.VouchType == "48") || (spec.Flag == "AP" && spec.VouchType == "49");
            return spec.Close && side && doc.Col("src_flag") == "C" && doc.Col("co_type") == "50";
        }

        // 票据按 iCloseID = 收款单主键找（分包票据的收款单票据号是「票据号-起-止」），再由 NoteRefusal 核对票据号。
        static void RefuseNoteUndo(object conn, ArapSpec spec, ArapDoc doc)
        {
            NoteRow note = NotesRegSql.ByReceipt(conn, spec.Flag, doc.Id);
            string why = NoteUndoRefusal(doc, note);
            if (why.Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", why);
            }
        }

        // 纯判断（--selftest 覆盖）：不能弃审时返回原因，可以返回空串。往来明细、结账由 RefuseUndo 另查。
        internal static string NoteUndoRefusal(ArapDoc doc, NoteRow note)
        {
            string why = NoteReceiptRefusal(doc);
            return why.Length > 0 ? why : NoteRefusal(doc, note);
        }

        static string NoteReceiptRefusal(ArapDoc doc)
        {
            if (doc.Col("voucher").Length > 0)
            {
                return "单据已生成凭证，不能弃审";
            }
            if (doc.Flag("start_flag"))
            {
                return "只能处理手工录入的单据，不能弃审";
            }
            if (doc.Flag("from_bank") || doc.Flag("to_bank"))
            {
                return "单据来自网银，不能弃审";
            }
            string code = doc.Col("note_no");
            if (code.Length == 0 || code != doc.Col("co_id"))
            {
                return "收款单的票据号与来源票据号不一致，不能弃审，请在 U8 中核对";
            }
            return Settled(doc) ? "单据已核销，不能弃审" : "";
        }

        static string NoteRefusal(ArapDoc doc, NoteRow note)
        {
            string label = "票据 " + doc.Col("note_no");
            if (note == null || note.CloseId != doc.Id)
            {
                return label + " 不存在或已不对应本收款单，不能弃审";
            }
            if (!NoteSplit.Matches(doc.Col("note_no"), note))
            {
                return label + " 与票据 " + note.Code + " 的票据号或子票区间不一致，不能弃审，请在 U8 中核对";
            }
            if (note.Opening)
            {
                return label + " 是期初票据，不能弃审其收款单";
            }
            if (note.Subs > 0 || note.Remain != note.Amount)
            {
                return label + " 已有处理记录（结算、贴现、背书等），不能弃审其收款单";
            }
            if (note.Change != 0)
            {
                return label + " 已换票，不能弃审其收款单";
            }
            return "";
        }

        static bool FromElsewhere(ArapDoc doc)
        {
            string src = doc.Col("src_flag");
            if (src.Length > 0 && src != "A")
            {
                return true;
            }
            return doc.Col("note_no").Length > 0 || doc.Flag("from_bank") || doc.Flag("to_bank");
        }

        public static bool Settled(ArapDoc doc)
        {
            if (doc.Flag("settled") || doc.Col("settler").Length > 0)
            {
                return true;
            }
            string lines = doc.Col("line_settled");
            return lines.Length > 0 && lines != "0";
        }

        // 本单为主或为对方的往来明细。弃审时排除本单自己的审核行（cProcStyle = 单据类型且 cCancelNo = 本单审核号）。
        internal static bool Processed(object conn, ArapSpec spec, ArapDoc doc, bool exceptOwn)
        {
            string sql = "select top 1 convert(varchar(20), d.Auto_ID) from " + spec.Detail + " d where "
                + "((d.cVouchType=? and d.cVouchID=?) or (d.cCoVouchType=? and d.cCoVouchID=?))";
            if (!exceptOwn)
            {
                return Rows.Scalar(conn, sql, new object[] { spec.VouchType, doc.Code, spec.VouchType, doc.Code }) != null;
            }
            sql = sql + " and (isnull(d.cProcStyle,N'')<>? or isnull(d.cCancelNo,N'')<>? or isnull(d.cPZid,N'')<>N'')";
            object[] args = new object[] { spec.VouchType, doc.Code, spec.VouchType, doc.Code, spec.VouchType, OwnNo(spec, doc) };
            return Rows.Scalar(conn, sql, args) != null;
        }

        // 审核行的 cCancelNo：收付款单 cFlag+cVouchType+cVouchID（AR48SK…），应收/应付单 JZ+cVouchType+cVouchID。
        public static string OwnNo(ArapSpec spec, ArapDoc doc)
        {
            if (spec.Close)
            {
                return spec.Flag + spec.VouchType + doc.Code;
            }
            return "JZ" + spec.VouchType + doc.Code;
        }

        // 审核行登记期间（年取 dRegDate）在 GL_mend 上 AR/AP 已结账。
        static bool PeriodClosed(object conn, ArapSpec spec, ArapDoc doc)
        {
            string sql = "select top 1 convert(varchar(10), d.iPeriod) from " + spec.Detail + " d inner join GL_mend m "
                + "on m.iyear=year(d.dRegDate) and m.iperiod=d.iPeriod where d.cVouchType=? and d.cVouchID=? "
                + "and d.cCancelNo=? and isnull(m." + spec.MendFlag + ",0)<>0";
            object[] args = new object[] { spec.VouchType, doc.Code, OwnNo(spec, doc) };
            return Rows.Scalar(conn, sql, args) != null;
        }
    }
}
