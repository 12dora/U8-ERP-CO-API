using System;
using System.Collections.Generic;

namespace U8Co
{
    // K12 退货单 ← 退货申请单（vouchers/generate，type=sale_return，source_type=sale_return_apply，id 是申请单 ID，
    // source_line_id 是申请单行 AutoID）。申请单须已审核、未关闭；所选行都挂着同一张蓝字发货单的行（iDLsID）。
    // 走参照发货单退货的同一条路（SaleReturnGen.cs）：来源换成该发货单、行换成申请行指的发货行，另在退货行上挂
    // irtnappid = 申请行 AutoID、crtnappcode = 申请单号（U8 的退货单就是这样指回申请单）。
    // 退货单表头 bneedbill 取申请行的 bneedbill（所选行须一致；head.invoiced 给了须与之相同），可退数量照它核对。
    // 事务里带 UPDLOCK 重查申请单表头、蓝字发货单表头与发货行（已审核、未关闭）和申请行，记下申请行 fretqty，提交前核对 U8 回写：申请行 fretqty 与数量同为负，退货 qty 后 fretqty 减 qty；
    // 删除这张退货单时加回（SaleReturnDel.cs）。不符 409 回滚。
    internal static partial class SaleGen
    {
        const string ApplyHeadSql = "select cCode, cVerifier, cCloser from SA_ReturnsApplyMain where ID=?";
        const string ApplyLinesSql = "select a.AutoID, isnull(a.iDLsID,0) as dl, isnull(d.DLID,0) as dlid, a.cSCloser, a.cWhCode, "
            + "convert(varchar(5), a.bneedbill) as nb, "
            + "convert(varchar(40), isnull(a.iQuantity,0)) as q, convert(varchar(40), isnull(a.fretqty,0)) as f "
            + "from SA_ReturnsApplyDetail a left join DispatchLists d on d.iDLsID=a.iDLsID where a.ID=?";
        const string ApplyLockSql = "select cSCloser, convert(varchar(40), isnull(iQuantity,0)) as q, "
            + "convert(varchar(40), isnull(fretqty,0)) as f from SA_ReturnsApplyDetail with (updlock, holdlock) where AutoID=?";
        const string ApplyHeadLockSql = "select cVerifier, cCloser, '' as cSCloser from SA_ReturnsApplyMain with (updlock, holdlock) "
            + "where ID=(select ID from SA_ReturnsApplyDetail where AutoID=?)";
        const string ApplyDispLockSql = "select h.cVerifier, h.cCloser, d.cSCloser from DispatchList h with (updlock, holdlock) "
            + "inner join DispatchLists d on d.DLID=h.DLID where d.iDLsID=?";
        const string ApplyNowSql = "select convert(varchar(40), isnull(fretqty,0)) as f from SA_ReturnsApplyDetail where AutoID=?";
        const string ApplyLinkSql = "select count(*) from DispatchLists x inner join DispatchList xh on xh.DLID=x.DLID "
            + "where xh.cDLCode=? and xh.bReturnFlag=1 and x.irtnappid=? and x.crtnappcode=?";

        public static ApiResult ReturnFromApply(WorkContext ctx, VoucherKind kind, int applyId, Dictionary<string, object> head,
            object[] lines)
        {
            if (!SaleReturn.Is(kind))
            {
                throw new BridgeException(400, "bad_request", "该单据类型不支持生单");
            }
            CheckReturn(head, lines);
            string appCode = ApplyHead(ctx.Conn, applyId);
            Dictionary<int, Dictionary<string, object>> body = IndexBy(ctx.Conn, ApplyLinesSql, applyId, "AutoID");
            int[] appLines = new int[lines.Length];
            int dlid = 0;
            object[] mapped = new object[lines.Length];
            for (int i = 0; i < lines.Length; i++)
            {
                mapped[i] = MapApplyLine((Dictionary<string, object>)lines[i], body, appLines, i, ref dlid);
            }
            RetJob job = new RetJob();
            job.Ctx = ctx;
            job.Kind = kind;
            job.SourceId = dlid;
            job.Head = WithoutInvoiced(head);
            job.DlCode = PlanReturnHead(ctx.Conn, dlid);
            job.Lines = PlanReturn(ctx.Conn, dlid, mapped);
            for (int i = 0; i < job.Lines.Count; i++)
            {
                job.Lines[i].AppLine = appLines[i];
                job.Lines[i].AppCode = appCode;
            }
            job.Invoiced = DecideInvoiced(job.Lines, ApplyInvoiced(body, appLines, RawKey(head, "invoiced")));
            job.EchoKind = ReturnsApplyRead.KindName;
            job.EchoId = applyId;
            DocMark.Touched(ReturnsApplyRead.KindName, applyId);
            return BuildReturn(job);
        }

        // 所选申请行的 bneedbill（都为空时返回调用方的 invoiced，交 DecideInvoiced 照旧决定）。行间不一致、与 invoiced 不同 400。
        static object ApplyInvoiced(Dictionary<int, Dictionary<string, object>> body, int[] appLines, object requested)
        {
            string flag = "";
            for (int i = 0; i < appLines.Length; i++)
            {
                string nb = CoRows.Col(body[appLines[i]], "nb");
                if (nb.Length > 0 && flag.Length > 0 && nb != flag)
                {
                    throw BridgeException.BadField("lines", "所选退货申请行的开票标志（bneedbill）不一致，请分开生成");
                }
                flag = nb.Length > 0 ? nb : flag;
            }
            if (flag.Length == 0)
            {
                return requested;
            }
            bool want = flag == "1";
            if (requested is bool && (bool)requested != want)
            {
                throw BridgeException.BadField("head.invoiced", "须与退货申请单的开票标志一致（" + (want ? "已开票" : "未开票") + "）");
            }
            return want;
        }

        // 申请单须已审核、未关闭；返回单号。
        static string ApplyHead(object conn, int applyId)
        {
            Dictionary<string, object> app = Rows.One(conn, ApplyHeadSql, new object[] { applyId });
            if (app == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            if (CoRows.Col(app, "cVerifier").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (CoRows.Col(app, "cCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            return CoRows.Col(app, "cCode");
        }

        // 申请行 → 退货生单行：source_line_id 换成申请行指的发货行，没给仓库时用申请行的仓库。
        static Dictionary<string, object> MapApplyLine(Dictionary<string, object> map,
            Dictionary<int, Dictionary<string, object>> body, int[] appLines, int index, ref int dlid)
        {
            Dictionary<string, object> row = ApplyRowOf(map, body, appLines, index);
            int rowDl = CoRows.AsId(CoRows.Col(row, "dlid"));
            if (dlid > 0 && dlid != rowDl)
            {
                throw new BridgeException(400, "bad_request", "一次只能参照同一张发货单的退货申请行");
            }
            dlid = rowDl;
            if (QtyOf(RawKey(map, "quantity")) > ApplyLeft(row))
            {
                throw new BridgeException(409, "state_mismatch", "超过退货申请单的可退数量");
            }
            Dictionary<string, object> copy = new Dictionary<string, object>(map);
            copy.Remove(KeyOf(copy, "source_line_id"));
            copy["source_line_id"] = CoRows.AsId(CoRows.Col(row, "dl"));
            string wh = CoRows.Col(row, "cWhCode");
            if (TextOf(map, "cwhcode").Length == 0 && wh.Length > 0)
            {
                copy.Remove(KeyOf(copy, "cwhcode"));
                copy["cwhcode"] = wh;
            }
            return copy;
        }

        // 请求行指的申请行：属于本单、不重复、未关闭、挂着原发货行。
        static Dictionary<string, object> ApplyRowOf(Dictionary<string, object> map,
            Dictionary<int, Dictionary<string, object>> body, int[] appLines, int index)
        {
            int appLine = CoRows.AsId(RawKey(map, "source_line_id"));
            Dictionary<string, object> row;
            if (appLine <= 0 || !body.TryGetValue(appLine, out row))
            {
                throw BridgeException.BadField("lines." + index + ".source_line_id", "明细行不存在");
            }
            if (Array.IndexOf(appLines, appLine) >= 0)
            {
                throw BridgeException.BadField("lines." + index + ".source_line_id", "明细行重复");
            }
            appLines[index] = appLine;
            if (CoRows.Col(row, "cSCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (CoRows.AsId(CoRows.Col(row, "dl")) <= 0 || CoRows.AsId(CoRows.Col(row, "dlid")) <= 0)
            {
                throw new BridgeException(400, "bad_request", "退货申请单行没有参照发货单，不能生成退货单");
            }
            return row;
        }

        static string KeyOf(Dictionary<string, object> map, string name)
        {
            foreach (string key in map.Keys)
            {
                if (Same(key, name))
                {
                    return key;
                }
            }
            return name;
        }

        // 申请行剩余可退 = |iQuantity| − |fretqty|。
        static decimal ApplyLeft(Dictionary<string, object> row)
        {
            decimal left = Math.Abs(RoomNum(row, "q")) - Math.Abs(RoomNum(row, "f"));
            return left < 0m ? 0m : left;
        }

        static SaveEcho RetEcho(RetJob job)
        {
            if (job.EchoKind != null && job.EchoKind.Length > 0)
            {
                return Echo(IdSql, "DLID", job.EchoKind, job.EchoId);
            }
            return Echo(IdSql, "DLID", "dispatch", job.SourceId);
        }

        // PinReturnLine 调用：参照退货申请单时挂申请行与申请单号。
        static void PinApply(object body, object row, RetLine line, List<string> schema)
        {
            if (line.AppLine <= 0)
            {
                return;
            }
            DomRows.Set(body, row, "irtnappid", Id(line.AppLine), schema);
            DomRows.Set(body, row, "crtnappcode", line.AppCode ?? "", schema);
        }

        // ReadReturnBase 调用（事务里）：带 UPDLOCK 重查申请单表头、蓝字发货单表头和发货行仍已审核、未关闭，
        // 申请行未关闭、可退数量，记下 fretqty 基准。
        static void LockApply(object conn, RetLine line)
        {
            if (line.AppLine <= 0)
            {
                return;
            }
            RequireOpenUnder(Rows.One(conn, ApplyHeadLockSql, new object[] { line.AppLine }), "退货申请单不存在");
            RequireOpenUnder(Rows.One(conn, ApplyDispLockSql, new object[] { line.LineId }), "发货单行不存在");
            Dictionary<string, object> row = Rows.One(conn, ApplyLockSql, new object[] { line.AppLine });
            if (row == null)
            {
                throw new BridgeException(409, "state_mismatch", "退货申请单行不存在");
            }
            if (CoRows.Col(row, "cSCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
            if (line.Qty > ApplyLeft(row))
            {
                throw new BridgeException(409, "state_mismatch", "超过退货申请单的可退数量");
            }
            line.AppBefore = RoomNum(row, "f");
        }

        static void RequireOpenUnder(Dictionary<string, object> row, string missing)
        {
            if (row == null)
            {
                throw new BridgeException(409, "state_mismatch", missing);
            }
            if (CoRows.Col(row, "cVerifier").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据未审核");
            }
            if (CoRows.Col(row, "cCloser").Length > 0 || CoRows.Col(row, "cSCloser").Length > 0)
            {
                throw new BridgeException(409, "state_mismatch", "单据已关闭");
            }
        }

        // RequireReturned 调用（提交前）：新退货行挂上了申请行，申请行 fretqty 减了本次退货数量。
        static void RequireApplied(object conn, RetJob job, RetLine line)
        {
            if (line.AppLine <= 0)
            {
                return;
            }
            string linked = Rows.Scalar(conn, ApplyLinkSql, new object[] { job.Code, line.AppLine, line.AppCode ?? "" });
            if (PuInv.Num(linked) < 1m)
            {
                NoteBack(job.Ctx.Item, "退货单 " + job.Code + " 没有行指向申请行" + Id(line.AppLine));
                throw new BridgeException(409, "u8_rejected", "U8 没有保存退货单与退货申请单的关联");
            }
            RequireApplyMoved(conn, job.Ctx.Item, line.AppLine, line.AppBefore, -line.Qty);
        }

        // 删除退货单（ReadDeleteBase）：申请行 → {fretqty 基准, 应加回的数量}，带 UPDLOCK。
        static void AddApp(object conn, Dictionary<int, decimal[]> apps, int appLine, decimal qty)
        {
            if (appLine <= 0)
            {
                return;
            }
            decimal[] slot;
            if (!apps.TryGetValue(appLine, out slot))
            {
                Dictionary<string, object> row = Rows.One(conn, ApplyLockSql, new object[] { appLine });
                slot = new decimal[] { row == null ? 0m : RoomNum(row, "f"), 0m, row == null ? 0m : 1m };
                apps[appLine] = slot;
            }
            slot[1] = slot[1] + qty;
        }

        // 申请单已被删（理论上不会：申请单有下游时不能删）的行不核对。
        static void RequireAppsBack(object conn, WorkItem item, Dictionary<int, decimal[]> apps)
        {
            foreach (KeyValuePair<int, decimal[]> pair in apps)
            {
                if (pair.Value[2] > 0m)
                {
                    RequireApplyMoved(conn, item, pair.Key, pair.Value[0], pair.Value[1]);
                }
            }
        }

        static void RequireApplyMoved(object conn, WorkItem item, int appLine, decimal before, decimal delta)
        {
            Dictionary<string, object> row = Rows.One(conn, ApplyNowSql, new object[] { appLine });
            decimal now = row == null ? 0m : RoomNum(row, "f");
            if (Near(now, before + delta))
            {
                return;
            }
            NoteBack(item, "申请行" + Id(appLine) + " fretqty " + Num(before) + "→" + Num(now) + " 应变 " + Num(delta));
            throw new BridgeException(409, "u8_rejected", "U8 没有回写退货申请单的累计退货数量");
        }
    }
}
