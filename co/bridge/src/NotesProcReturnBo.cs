using System;
using System.Collections.Generic;

namespace U8Co
{
    // 票据退回生成应收单（应付单）用的 UFAPBO clsAPVouch：与 vouchers/create 的 ar_bill / ap_bill 同一条路径（ArapCo.Create）。
    // 组件在开事务之前打开、取模板（Init 按引用拿登录对象，先 DropLogin），表头表体在事务里按计划填好后 SaveVouch（同票据登记的收款单，
    // NotesRegLink.Create）。主键（Auto_ID）和单号由 U8 分配。
    internal sealed class NotesProcReturnBo : IDisposable
    {
        public VoucherKind Kind;
        public ArapSpec Spec;
        ArapBo _bo;
        readonly object[] _doms = new object[2];

        public static NotesProcReturnBo Open(WorkContext ctx, string flag)
        {
            NotesProcReturnBo made = new NotesProcReturnBo();
            try
            {
                made.Kind = Kinds.Find(NotesProcReq.BillKind(flag));
                made.Spec = ArapReq.Spec(made.Kind);
                made._doms[0] = Rows.NewDom();
                made._doms[1] = Rows.NewDom();
                made._bo = ArapBo.Open(ctx, made.Spec);
                ArapCo.Template(ctx, made._bo, made.Spec, made._doms);
                return made;
            }
            catch
            {
                made.Dispose();
                throw;
            }
        }

        public object Head
        {
            get { return _doms[0]; }
        }

        // 事务里：填表头表体后 SaveVouch。调用前后 @@TRANCOUNT 变了（组件自行提交或回滚）→ 504；U8 返回 false → 409 u8_rejected。
        public void Save(WorkContext ctx, NotesProcPlan plan)
        {
            ArapInput input = Input(plan);
            ArapDom.FillHead(_doms[0], Spec, input, plan.Operator);
            ArapDom.FillBody(_doms[1], Spec, input);
            string before = CoTrans.Count(ctx.Conn);
            string msg;
            bool ok = _bo.Save(_doms, out msg);
            string after = CoTrans.Count(ctx.Conn);
            CoRows.Note(ctx.Item, "SaveVouch " + Spec.VouchType + " " + (ok ? "true" : "false") + " @@TRANCOUNT " + before + " → " + after
                + (msg.Length > 0 ? " " + msg : ""));
            string bill = NotesProcReturn.BillTitle(plan.Flag);
            if (after != before)
            {
                throw new BridgeException(504, "outcome_unknown", "U8 " + bill + "组件改变了事务，结果未知；请先在 U8 里核对票据 "
                    + plan.Note.Code + " 和处理号 " + plan.CancelNo);
            }
            if (!ok)
            {
                throw new BridgeException(409, "u8_rejected", ArapCo.Said(msg, "U8 没有保存退回生成的" + bill));
            }
        }

        // 新应收单（应付单）的输入：往来单位 = 票据的客户（供应商，cEndorser），部门、业务员取票据，日期 = 处理日期，
        // 表头科目 = 往来控制科目、摘要「转出票据{票据号}」；表体一行，金额 = 本次处理金额、税率 0、摘要「票据转出」，
        // 不写表体科目（同 U8，表体 cCode 为空）。本位币、汇率 1（外币票据在闸门已拒绝）。
        internal static ArapInput Input(NotesProcPlan plan)
        {
            NoteHead note = plan.Note;
            ArapInput input = new ArapInput();
            input.Head = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            input.Head["cdwcode"] = note.Partner;
            Opt(input.Head, "cdeptcode", note.Dept);
            Opt(input.Head, "cperson", note.Person);
            input.Head["ccode"] = plan.CtrlKm;
            input.Head["cdigest"] = NotesProcReturnSql.HeadDigest(note.Code);
            input.Date = plan.Date;
            input.Currency = note.Currency;
            input.Home = plan.Local;
            input.Rate = 1m;
            input.Sum = plan.Amount;
            input.SumF = plan.Amount;
            ArapLine line = new ArapLine();
            line.Fields = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            line.Fields["cdigest"] = "票据转出";
            line.Amt = plan.Amount;
            line.AmtF = plan.Amount;
            line.Orig = plan.Amount;
            input.Lines = new List<ArapLine>();
            input.Lines.Add(line);
            return input;
        }

        static void Opt(Dictionary<string, string> head, string key, string value)
        {
            if (!string.IsNullOrEmpty(value) && value.Trim().Length > 0)
            {
                head[key] = value.Trim();
            }
        }

        public void Dispose()
        {
            ComUtil.Final(_doms[1]);
            ComUtil.Final(_doms[0]);
            _doms[0] = null;
            _doms[1] = null;
            ArapCo.Close(_bo);
            _bo = null;
        }
    }
}
