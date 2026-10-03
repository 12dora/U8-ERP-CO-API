using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 应收 / 应付期初单据（openings/arap，U8「期初单据录入」AR0306 / AP0306）。
    // 期初单据就是 Ap_Vouch 里 bStartFlag=1 的应收单 R0 / 应付单 P0：只有表头（Ap_Vouchs 没有行），
    // 单据日期 = 启用日期前一天，审核后与普通单据一样写往来明细。新增、审核、弃审、删除都走 ar_bill / ap_bill 的 UFAPBO 路径
    // （ArapCo），请求连接上的 CoTrans 包住，预演（rollback）由提交钩子回滚。普通 ar_bill / ap_bill 路由照旧拒绝期初单据。
    // 只对配置为测试账套的账套开放（TestAccountGate）。
    internal static class OpeningsArap
    {
        public static ApiResult Run(WorkContext ctx)
        {
            OpeningsArapAsk ask = OpeningsArapReq.Parse(ctx.Item.Body);
            // 入队后再查一次测试账套名单（登录前已查过）。
            TestAccountGate.Require(ctx.Item, TestAccountGate.OpeningsArapText);
            PermCheck.RequireRule(PermCheck.Of(ctx), PermRegistry.ForKey(OpeningsArapReq.RuleKey(ask)));
            VoucherKind kind = Kinds.Find(OpeningsArapReq.KindOf(ask));
            ArapSpec spec = ArapReq.Spec(kind);
            OpeningsArapGuard.Check(ctx, ask.Side, ask.Action);
            ApiResult result;
            if (ask.Create)
            {
                result = Create(ctx, ask, kind, spec);
            }
            else if (ask.Action == "delete")
            {
                result = Delete(ctx, ask, kind, spec);
            }
            else
            {
                result = Verify(ctx, ask, kind, spec);
            }
            result.Body["side"] = ask.Side;
            result.Body["opening"] = true;
            return result;
        }

        static ApiResult Create(WorkContext ctx, OpeningsArapAsk ask, VoucherKind kind, ArapSpec spec)
        {
            string start = ReportsOpening.StartDate(ctx, ask.Side);
            if (start.Length == 0)
            {
                throw Refuse((ask.Side == "ar" ? "应收" : "应付") + "款管理未启用，没有启用日期");
            }
            ArapInput input = Input(ctx, ask, spec, start);
            Check(ctx.Conn, spec, ask, input, start);
            CoRows.Note(ctx.Item, "期初单据 " + ask.Side + " 启用 " + start + " 单据日期 " + input.Date);
            ArapBo bo = null;
            object[] doms = new object[2];
            try
            {
                doms[0] = Rows.NewDom();
                doms[1] = Rows.NewDom();
                bo = ArapBo.Open(ctx, spec);
                ArapCo.Template(ctx, bo, spec, doms);
                if (ArapDom.Find(DomRows.Schema(doms[0]), "bStartFlag") == null)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 单据模板没有期初标志 bStartFlag");
                }
                ArapDom.FillHead(doms[0], spec, input, ctx.Session.OperatorName);
                Save(ctx, bo, kind, spec, doms);
                ApiResult result = ArapCo.Created(ctx, kind, spec, doms[0]);
                result.Body["start_date"] = start;
                result.Body["date"] = input.Date;
                return result;
            }
            finally
            {
                ComUtil.Final(doms[1]);
                ComUtil.Final(doms[0]);
                ArapCo.Close(bo);
            }
        }

        // 表体 DOM 只有模板 schema、没有行：只存表头。保存后在本事务里核对 bStartFlag 已写入，否则回滚。
        static void Save(WorkContext ctx, ArapBo bo, VoucherKind kind, ArapSpec spec, object[] doms)
        {
            ArapCo.InTrans(ctx, "SaveVouch", delegate(out string m)
            {
                if (!bo.Save(doms, out m))
                {
                    return false;
                }
                if (!SavedAsOpening(ctx, kind, spec, doms[0]))
                {
                    m = "U8 保存的单据没有期初标志，已回滚";
                    return false;
                }
                ArapCo.MarkNew(ctx, kind, spec, doms[0]);
                return true;
            });
        }

        // 主键取表头 DOM，缺了按单号找；都找不到时不在这里拦（提交后的回读会报 504）。
        static bool SavedAsOpening(WorkContext ctx, VoucherKind kind, ArapSpec spec, object headDom)
        {
            List<Dictionary<string, object>> rows = Rows.FromDom(headDom, 1);
            if (rows == null || rows.Count == 0)
            {
                return true;
            }
            int id = CoRows.AsId(CoRows.Col(rows[0], kind.IdColumn));
            string code = CoRows.Col(rows[0], kind.CodeColumn);
            if (id <= 0 && code.Length > 0)
            {
                id = ArapSql.IdByCode(ctx.Conn, kind, spec, code);
            }
            ArapDoc doc = id > 0 ? ArapSql.Head(ctx.Conn, kind, spec, id) : null;
            return doc == null || doc.Flag("start_flag");
        }

        static ArapInput Input(WorkContext ctx, OpeningsArapAsk ask, ArapSpec spec, string start)
        {
            ArapInput input = new ArapInput();
            input.Home = ctx.HomeCurrency ?? "";
            input.Currency = ask.Currency.Length > 0 ? ask.Currency : input.Home;
            input.Date = DayBefore(start);
            input.Lines = new List<ArapLine>();
            Money(ask, input);
            input.Head = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            input.Head["cdwcode"] = ask.Partner;
            input.Head["ccode"] = ask.Account;
            input.Head["cdigest"] = ask.Digest.Length > 0 ? ask.Digest : (spec.Flag == "AR" ? "期初应收" : "期初应付");
            if (ask.Department.Length > 0)
            {
                input.Head["cdeptcode"] = ask.Department;
            }
            if (ask.Person.Length > 0)
            {
                input.Head["cperson"] = ask.Person;
            }
            OpeningMarks(input.Head, spec, ask.Amount < 0);
            return input;
        }

        // 期初标志和方向，全在这里（实测后只改这一处）。表头字段经 ArapDom.FillHead 的 Generic 写入，覆盖 FillHead 的缺省方向。
        // U8 的期初单据金额都是正数，方向看 bd_c：应收单借方 1 为正常，贷方 0 为反方向；应付单相反。负数金额取绝对值、方向取反。
        internal static void OpeningMarks(Dictionary<string, string> head, ArapSpec spec, bool reverse)
        {
            head["bstartflag"] = "True";
            bool debit = spec.Flag == "AR" ? !reverse : reverse;
            head["bd_c"] = debit ? "True" : "False";
        }

        // 本位币：汇率只能不给或 1，原币 = 本币。外币：必须给汇率，本币 = round(原币 × 汇率, 2)。
        static void Money(OpeningsArapAsk ask, ArapInput input)
        {
            decimal amount = Math.Abs(ask.Amount);
            if (ArapReq.IsHome(input))
            {
                if (ask.Rate != 0m && ask.Rate != 1m)
                {
                    throw BridgeException.BadField("exch_rate", input.Currency + "单据的汇率必须是 1");
                }
                input.Rate = 1m;
                input.Sum = amount;
                input.SumF = amount;
                return;
            }
            if (ask.Rate == 0m)
            {
                throw BridgeException.BadField("exch_rate", "外币必须给汇率 exch_rate");
            }
            input.Rate = ask.Rate;
            input.SumF = amount;
            input.Sum = decimal.Round(amount * ask.Rate, 2, MidpointRounding.AwayFromZero);
            if (input.Sum <= 0m || input.Sum > 1000000000000m)
            {
                throw BridgeException.BadField("amount", "amount 折算本币后必须大于 0 且不超过 1000000000000");
            }
        }

        // 档案检查：币种、部门（末级）、业务员、往来单位（启用日期前未停用）、科目（启用年度、末级、本系统受控）。
        // 不查单据月份是否结账（单据在启用日期之前，期初的闸门在 OpeningsArapGuard）。
        static void Check(object conn, ArapSpec spec, OpeningsArapAsk ask, ArapInput input, string start)
        {
            ArapArch.Need(conn, "select top 1 cexch_name from foreigncurrency where cexch_name=?", input.Currency, "币种");
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            ArapArch.Refs(conn, input.Head, "cdeptcode", "cperson", seen);
            ArapRefs.Party(conn, spec, ask.Partner, start);
            ArapRefs.Subject(conn, ReportsOpening.YearOf(start, 0), ask.Account, spec.Flag, seen);
        }

        internal static string DayBefore(string start)
        {
            DateTime day = DateTime.ParseExact(start, "yyyy-MM-dd", CultureInfo.InvariantCulture);
            return day.AddDays(-1).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        static ApiResult Verify(WorkContext ctx, OpeningsArapAsk ask, VoucherKind kind, ArapSpec spec)
        {
            ArapDoc doc = Need(ctx, kind, spec, ask.Id);
            bool undo = ask.Action == "unverify";
            bool verified = doc.Col("verifier").Length > 0;
            if (!undo && verified)
            {
                throw Refuse("单据已审核");
            }
            if (undo && !verified)
            {
                throw Refuse("单据未审核");
            }
            if (undo)
            {
                RefuseUsed(ctx.Conn, spec, doc, true);
            }
            if (undo)
            {
                SignOpening(ctx, spec, doc, ctx.Item.Date, true);
            }
            else
            {
                SignOpening(ctx, spec, doc, DayBefore(ReportsOpening.StartDate(ctx, ask.Side)), false);
            }
            return ArapCo.AfterCommit(ctx, "已提交但未能回读审核状态", delegate
            {
                return ArapCo.Verified(ctx, kind, spec, ask.Id, ask.Action);
            });
        }

        // UFAPBO.Sign 按登录日期写往来明细的期间和日期；U8 期初单据是第 0 期、登记与审核日期为启用日前一天，
        // 同一事务里审核成功后改成这个形态。第 0 期在 U8 看来已结账，CancelSign 会拒绝，弃审前先把本单的审核行改回登录月，
        // 再由 CancelSign 照常处理（day 是登录日期）。
        static void SignOpening(WorkContext ctx, ArapSpec spec, ArapDoc doc, string day, bool undo)
        {
            ArapBo bo = null;
            try
            {
                bo = ArapBo.Open(ctx, spec);
                ArapBo runner = bo;
                string method = undo ? "CancelSign" : "Sign";
                string cond = undo ? ArapCond.Unsign(spec, doc) : ArapCond.Sign(spec, doc);
                ArapCo.InTrans(ctx, method, delegate(out string m)
                {
                    if (undo && !Stamp(ctx.Conn, spec, doc, day, true))
                    {
                        m = MismatchText;
                        return false;
                    }
                    bool ok = runner.Run(method, cond, out m);
                    if (ok && !undo && !Stamp(ctx.Conn, spec, doc, day, false))
                    {
                        m = MismatchText;
                        return false;
                    }
                    return ok;
                });
            }
            finally
            {
                ArapCo.Close(bo);
            }
        }

        const string MismatchText = "U8 审核行与预期不符，已回滚";
        const string OwnRowsWhere = " WHERE cVouchType=? AND cVouchID=? AND cProcStyle=? AND cCancelNo=?";

        // 只改本单自己的审核行（cProcStyle = 单据类型、cCancelNo = JZ + 类型 + 单号）；找不到这样的行返回 false，调用方回滚。
        static bool Stamp(object conn, ArapSpec spec, ArapDoc doc, string day, bool undo)
        {
            object[] own = new object[] { spec.VouchType, doc.Code, spec.VouchType, "JZ" + spec.VouchType + doc.Code };
            string count = Rows.Scalar(conn, "SELECT COUNT(*) FROM " + spec.Detail + OwnRowsWhere, own);
            if (count == null || count.Trim() == "0")
            {
                return false;
            }
            List<object> args = new List<object>();
            string period = "0";
            if (undo)
            {
                period = "MONTH(CONVERT(date, ?, 23))";
                args.Add(day);
            }
            args.AddRange(new object[] { day, day });
            args.AddRange(own);
            string sql = "UPDATE " + spec.Detail + " SET iPeriod=" + period + ", dRegDate=CONVERT(datetime, CONVERT(date, ?, 23)), "
                + "dPZDate=CONVERT(datetime, CONVERT(date, ?, 23)), isignseq=0" + OwnRowsWhere;
            GlSql.Exec(conn, sql, args.ToArray());
            if (!undo)
            {
                GlSql.Exec(conn, "UPDATE Ap_Vouch SET dVerifyDate=CONVERT(datetime, CONVERT(date, ?, 23)) WHERE Auto_ID=?",
                    new object[] { day, doc.Id });
            }
            return true;
        }

        static ApiResult Delete(WorkContext ctx, OpeningsArapAsk ask, VoucherKind kind, ArapSpec spec)
        {
            ArapDoc doc = Need(ctx, kind, spec, ask.Id);
            if (doc.Col("verifier").Length > 0 || doc.Col("verify_date").Length > 0)
            {
                throw Refuse("单据已审核");
            }
            RefuseUsed(ctx.Conn, spec, doc, false);
            ArapCo.Write(ctx, spec, "DeleteVouch", ArapCond.Delete(spec, doc));
            return ArapCo.AfterCommit(ctx, "已删除但未能回读确认", delegate
            {
                return ArapCo.Gone(ctx, kind, spec, doc);
            });
        }

        // 只处理期初单据：不存在 404，不是期初单据 409。
        static ArapDoc Need(WorkContext ctx, VoucherKind kind, ArapSpec spec, int id)
        {
            ArapDoc doc = ArapSql.Head(ctx.Conn, kind, spec, id);
            if (doc == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (!doc.Flag("start_flag"))
            {
                throw Refuse("不是期初单据");
            }
            PurchaseCo.RefuseFlow(ctx.Conn, kind, doc.Row);
            return doc;
        }

        // 弃审、删除前：未制单、非协同生成、未核销、没有别的往来明细（弃审时排除本单自己的审核行）。
        static void RefuseUsed(object conn, ArapSpec spec, ArapDoc doc, bool undo)
        {
            string tail = undo ? "不能弃审" : "不能删除";
            if (doc.Col("voucher").Length > 0)
            {
                throw Refuse("单据已生成凭证，" + tail);
            }
            if (doc.Col("co_type").Length > 0)
            {
                throw Refuse("只能处理手工录入的期初单据，" + tail);
            }
            if (ArapSql.Settled(doc))
            {
                throw Refuse("单据已核销，" + tail);
            }
            if (ArapSql.Processed(conn, spec, doc, undo))
            {
                throw Refuse(undo ? "单据已核销或做过后续处理，不能弃审" : "单据已有往来明细，不能删除");
            }
        }

        static BridgeException Refuse(string message)
        {
            return new BridgeException(409, "state_mismatch", message);
        }
    }

    // 期初单据的模块闸门（新增、删除、弃审，审核也经过这里）。
    internal static class OpeningsArapGuard
    {
        // 与库存期初同一规则（U8：「库存启用的第一个月已经月结,不可以增加删除期初!」）：模块启用月已结账后，
        // 期初单据不能再新增、删除、审核、弃审。
        const string ClosedSql = "SELECT TOP 1 'x' FROM GL_mend WHERE iyear=? AND iperiod=? AND ISNULL({COL},0)=1";

        public static void Check(WorkContext ctx, string side, string action)
        {
            string start = ReportsOpening.StartDate(ctx, side);
            if (start.Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", side == "ar" ? "应收款管理未启用" : "应付款管理未启用");
            }
            DateTime day = DateTime.ParseExact(start, "yyyy-MM-dd", CultureInfo.InvariantCulture);
            string col = side == "ar" ? "bflag_AR" : "bflag_AP";
            string hit = Rows.Scalar(ctx.Conn, ClosedSql.Replace("{COL}", col), new object[] { day.Year, day.Month });
            if (!string.IsNullOrEmpty(hit))
            {
                string module = side == "ar" ? "应收款管理" : "应付款管理";
                throw new BridgeException(409, "state_mismatch", module + "启用的第一个月已经结账，不能修改期初单据");
            }
        }
    }
}
