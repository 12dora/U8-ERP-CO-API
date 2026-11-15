using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 票据登记的计划：校验后的请求、登录日期、本位币、科目、票据卡片模板（iVT_ID，0 为没有），以及要生成的收款单（48）的输入。
    internal sealed class NoteRegPlan
    {
        public NoteRegAsk Ask;
        public string Date;
        public string Currency;
        public string PartnerName;
        public string Drawer;
        public string NoteKm;
        public string CtrlKm;
        public string Digest;
        public string Operator;
        public int VtId;
        public ArapInput Receipt;
    }

    // 票据登记前的检查（照 U8 票据组件 NoteCheck 的检查项由桥自己做）：
    // 签发、收票日期不晚于登录日期；收票日期在会计期间内、不早于应收（应付）启用日期、所在期间未结账；票据号没被用过；
    // 客户（应付票据是供应商）存在；部门末级、业务员存在、结算方式末级、票据科目末级、收付款单表体科目是本系统受控科目
    // （ArapRefs，同收付款单新增）。票据科目：请求的 note_km，否则基本科目 pjkm（应付取 cApCode，为空时必须给 note_km）。
    // 不符：日期 400，档案 400，其余 409 state_mismatch。
    internal static class NotesRegGate
    {
        public const string DefaultDigest = "票据登记";

        public static NoteRegPlan Create(WorkContext ctx, NoteRegAsk ask)
        {
            object conn = ctx.Conn;
            NoteRegPlan plan = new NoteRegPlan();
            plan.Ask = ask;
            plan.Date = ctx.Item.Date ?? "";
            plan.Operator = ctx.OperatorName;
            plan.Currency = ctx.HomeCurrency;
            Dates(ctx, plan);
            string taken = NotesRegSql.Taken(conn, ask.NoteNo, false);
            if (taken != null)
            {
                throw State(TakenMessage(taken, ask.NoteNo));
            }
            // 没有这张票据却有它的可用子票区间（以前删票据时没删干净的残留），登记后核对会不符，先拒绝。
            if (NotesProcSql.AvailTotal(conn, NoteInsert.Link(ask.Flag, ask.NoteNo))[1] > 0)
            {
                throw State("票据号 " + ask.NoteNo + " 留有可用子票区间（Ap_Note_AvailRange）的残留记录，请在 U8 中核对后再登记");
            }
            Partner(conn, plan);
            Accounts(conn, plan);
            plan.Digest = ask.Digest.Length > 0 ? ask.Digest : DefaultDigest;
            plan.VtId = NotesRegSql.VtId(conn, ask.Flag);
            plan.Receipt = Receipt(plan, ctx.HomeCurrency);
            ArapRefs.Check(conn, ArapReq.Spec(Kinds.Find(NotesRegReq.CloseKind(ask.Flag))), plan.Receipt);
            return plan;
        }

        // 往来单位：应收票据是客户，应付票据是供应商；不存在 400。出票人缺省为往来单位名称。
        static void Partner(object conn, NoteRegPlan plan)
        {
            NoteRegAsk ask = plan.Ask;
            bool ap = ask.Flag == "AP";
            string name = TransferSql.PartnerName(conn, ask.Flag, ask.Partner);
            if (name == null)
            {
                throw BridgeException.BadField(NotesReg.PartnerField(ask.Flag), (ap ? "供应商不存在 " : "客户不存在 ") + ask.Partner);
            }
            plan.PartnerName = name.Trim();
            plan.Drawer = ask.Drawer.Length > 0 ? ask.Drawer : plan.PartnerName;
        }

        // 票据科目（note_km 或基本科目 pjkm）、收付款单表体科目（km 或基本科目 kzkm），按收票日期的年度取。
        static void Accounts(object conn, NoteRegPlan plan)
        {
            NoteRegAsk ask = plan.Ask;
            string side = ask.Flag == "AP" ? "应付" : "应收";
            int year = int.Parse(ask.ReceiptDate.Substring(0, 4), CultureInfo.InvariantCulture);
            string basic = NotesRegSql.Km(conn, ask.Flag, "pjkm", year, plan.Currency);
            plan.NoteKm = ask.NoteKm.Length > 0 ? ask.NoteKm : basic;
            if (plan.NoteKm.Length == 0)
            {
                throw State(side + "款管理没有设置票据科目（基本科目 pjkm），请在 U8 客户端设置，或在 note_km 里给出票据科目");
            }
            NoteKmFree(conn, ask.NoteKm, basic, year);
            plan.CtrlKm = ask.Km.Length > 0 ? ask.Km : NotesRegSql.Km(conn, ask.Flag, "kzkm", year, plan.Currency);
            if (plan.CtrlKm.Length == 0)
            {
                throw State(side + "款管理没有设置" + side + "科目（基本科目 kzkm），请在 U8 客户端设置，或在 km 里给出"
                    + NotesReg.CloseTitle(ask.Flag) + "表体科目");
            }
        }

        // 请求给的票据科目不能是应收、应付的往来控制科目（code.cother 为 AR / AP，如应收账款），否则票据金额会记到往来控制科目上；
        // 与基本科目 pjkm 相同的放行（那是 U8 自己的票据科目设置）。
        internal static void NoteKmFree(object conn, string noteKm, string basic, int year)
        {
            if (noteKm.Length == 0 || string.Equals(noteKm, basic, StringComparison.OrdinalIgnoreCase))
            {
                return;
            }
            string other = NotesRegSql.Controlled(conn, year, noteKm);
            if (other == "AR" || other == "AP")
            {
                throw BridgeException.BadField("note_km", "票据科目不能是" + (other == "AR" ? "应收" : "应付") + "受控科目 " + noteKm);
            }
        }

        // 登录日期是 U8 的业务日期：签发、收票都不能晚于它；收票日期（= 收款单日期）在会计期间内、不早于启用日期、未结账。
        static void Dates(WorkContext ctx, NoteRegPlan plan)
        {
            NoteRegAsk ask = plan.Ask;
            if (plan.Date.Length != 10)
            {
                throw new BridgeException(400, "bad_request", "缺少登录日期 date");
            }
            if (string.CompareOrdinal(ask.SignDate, plan.Date) > 0)
            {
                throw BridgeException.BadField("sign_date", "签发日期不能晚于登录日期 " + plan.Date);
            }
            if (string.CompareOrdinal(ask.ReceiptDate, plan.Date) > 0)
            {
                throw BridgeException.BadField("receipt_date", "收票日期不能晚于登录日期 " + plan.Date);
            }
            object conn = ctx.Conn;
            int[] period = WriteoffSql.PeriodOf(conn, ctx.Item.Acc, ask.ReceiptDate);
            if (period == null)
            {
                throw State("收票日期不在 U8 的会计期间内");
            }
            DateTime start;
            DateTime day = DateTime.ParseExact(ask.ReceiptDate, "yyyy-MM-dd", CultureInfo.InvariantCulture);
            string side = ask.Flag == "AP" ? "应付" : "应收";
            if (WriteoffSql.StartDate(conn, ask.Flag, out start) && day < start)
            {
                throw State("收票日期早于" + side + "系统启用日期 " + start.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
            }
            if (WriteoffSql.Closed(conn, ask.Flag, period[0], period[1]))
            {
                throw State("收票日期所在期间" + side + "已结账");
            }
        }

        // 收款单（48，应付票据是付款单 49）的输入，与 vouchers/create 的 ar_receipt / ap_payment 同一套校验（ArapReq.CheckCreate）：
        // 表头往来单位、日期 = 收票日期、部门、业务员、结算方式 = 票据类型、摘要、出票银行（表头科目 = 票据科目：校验、档案检查照常，写 DOM 时去掉、保存后补，见 NotesRegLink、NotesRegSql.MarkSql）；
        // 表体一行应收款 / 应付款（iType 0），科目 = 往来控制科目，金额 = 票面金额。
        internal static ArapInput Receipt(NoteRegPlan plan, string home)
        {
            Dictionary<string, object> head;
            object[] lines;
            ReceiptFields(plan, out head, out lines);
            return ArapReq.CheckCreate(Kinds.Find(NotesRegReq.CloseKind(plan.Ask.Flag)), head, lines, home);
        }

        internal static void ReceiptFields(NoteRegPlan plan, out Dictionary<string, object> head, out object[] lines)
        {
            NoteRegAsk ask = plan.Ask;
            head = new Dictionary<string, object>();
            head["cdwcode"] = ask.Partner;
            head["dvouchdate"] = ask.ReceiptDate;
            head["cdeptcode"] = ask.Dept;
            if (ask.Person.Length > 0)
            {
                head["cperson"] = ask.Person;
            }
            head["csscode"] = ask.SettleCode;
            head["ccode"] = plan.NoteKm;
            head["cexch_name"] = plan.Currency;
            head["cdigest"] = plan.Digest;
            if (ask.DrawerBank.Length > 0)
            {
                head["cbank"] = ask.DrawerBank;
            }
            Dictionary<string, object> line = new Dictionary<string, object>();
            line["itype"] = "0";
            line["ckm"] = plan.CtrlKm;
            line["iamt"] = ArapReq.Money(ask.Amount);
            lines = new object[] { line };
        }

        internal static string TakenMessage(string taken, string no)
        {
            return taken == "note" ? "票据号已存在 " + no : "票据号已被收付款单引用 " + no;
        }

        static BridgeException State(string message)
        {
            return new BridgeException(409, "state_mismatch", message);
        }
    }
}
