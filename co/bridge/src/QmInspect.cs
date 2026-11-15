using System.Collections.Generic;

namespace U8Co
{
    // 来料报检单（QM01）、产品报检单（QM02）：只读，走 SQL，没有审批流。表头 QMINSPECTVOUCHER 按 CVOUCHTYPE 分，
    // 表体 QMINSPECTVOUCHERS 按 ID 挂。来源：QM01 表头 CSOURCEID 是到货单 ID、行 SOURCEAUTOID 是到货单行 Autoid；
    // QM02 表头 CSOURCEID 是生产订单 MoId、行 SOURCEAUTOID 是 MoDId。下游检验单（QM03 / QM04）的 INSPECTID / INSPECTAUTOID
    // 指回本单的 ID / AUTOID。其他报检单（QM11）没有来源，下游是其他检验单（QM15）。列取 U8 单据模板上的名字（报检人是 CMAKER，报检部门是 CINSPECTDEPCODE）。
    // 自定义项、自由项按表列名原样输出（表头 CDEFINE1–16，表体 CFREE1–10、CDEFINE22–37），同其他走 SQL 读取的类型。
    internal static class QmInspect
    {
        const int LineCap = 500;

        const string HeadSql = "SELECT h.ID, h.CVOUCHTYPE, h.CINSPECTCODE, h.DDATE, h.CTIME, h.CSOURCE, h.CSOURCECODE,"
            + " h.CSOURCEID, h.DARRIVALDATE, h.CPOCODE, h.CVENCODE, v.cVenName AS CVENNAME, h.CCUSCODE,"
            + " c.cCusName AS CCUSNAME, h.CDEPCODE, d.cDepName AS CDEPNAME, h.CINSPECTDEPCODE,"
            + " i.cDepName AS CINSPECTDEPNAME, h.CCHECKTYPECODE, h.BEXIGENCY, h.IPROORDERID, h.CPROORDERCODE,"
            + " h.CREJECTCODE, h.REJECTID, h.CMAKER, h.DMAKETIME, h.CMODIFIER, h.DMODIFYDATE, h.DMODIFYTIME,"
            + " h.CVERIFIER, h.DVERIFYDATE, h.DVERIFYTIME, h.CDEFINE1, h.CDEFINE2, h.CDEFINE3, h.CDEFINE4, h.CDEFINE5,"
            + " h.CDEFINE6, h.CDEFINE7, h.CDEFINE8, h.CDEFINE9, h.CDEFINE10, h.CDEFINE11, h.CDEFINE12, h.CDEFINE13,"
            + " h.CDEFINE14, h.CDEFINE15, h.CDEFINE16, h.UFTS"
            + " FROM QMINSPECTVOUCHER h"
            + " LEFT JOIN Vendor v ON v.cVenCode = h.CVENCODE"
            + " LEFT JOIN Customer c ON c.cCusCode = h.CCUSCODE"
            + " LEFT JOIN Department d ON d.cDepCode = h.CDEPCODE"
            + " LEFT JOIN Department i ON i.cDepCode = h.CINSPECTDEPCODE"
            + " WHERE h.ID=? AND h.CVOUCHTYPE=?";

        const string LineSql = "SELECT b.AUTOID, b.ID, b.SOURCEAUTOID, b.CINVCODE, n.cInvName AS CINVNAME,"
            + " n.cInvStd AS CINVSTD, n.cComUnitCode AS CCOMUNITCODE, b.CWHCODE, b.CBATCH, b.CPROBATCH, b.DPRODATE,"
            + " b.DVDATE, b.CUNITID, b.FCHANGRATE, b.FQUANTITY, b.FNUM, b.FSUMCHECKQTY, b.FSUMCHECKNUM, b.BFLAG,"
            + " b.ITESTSTYLE, b.BEXIGENCY, b.CPOCODE, b.CORDERCODE, b.IORDERDID, b.IORDERSEQ, b.IPROORDERID,"
            + " b.CPROORDERCODE, b.IPROORDERAUTOID, b.CDEPCODE, b.CCHECKCODE, b.CITEMCLASS, b.CITEMCODE,"
            + " b.CFREE1, b.CFREE2, b.CFREE3, b.CFREE4, b.CFREE5, b.CFREE6, b.CFREE7, b.CFREE8, b.CFREE9, b.CFREE10,"
            + " b.CDEFINE22, b.CDEFINE23, b.CDEFINE24, b.CDEFINE25, b.CDEFINE26, b.CDEFINE27, b.CDEFINE28, b.CDEFINE29,"
            + " b.CDEFINE30, b.CDEFINE31, b.CDEFINE32, b.CDEFINE33, b.CDEFINE34, b.CDEFINE35, b.CDEFINE36, b.CDEFINE37,"
            + " b.UFTS"
            + " FROM QMINSPECTVOUCHERS b"
            + " LEFT JOIN Inventory n ON n.cInvCode = b.CINVCODE"
            + " WHERE b.ID=? ORDER BY b.AUTOID";

        public static bool Handles(VoucherKind kind)
        {
            return kind != null && kind.HeadTable == "QMINSPECTVOUCHER";
        }

        public static ApiResult Load(WorkContext ctx, VoucherKind kind, int id)
        {
            if (!Handles(kind))
            {
                throw new BridgeException(400, "bad_request", "该单据类型不是报检单");
            }
            // 质量单据的 BizObjectId 就是表头 CVOUCHTYPE（QM01 / QM02）；报检单没有审批流，Workflow 为假。
            Dictionary<string, object> head = Rows.One(ctx.Conn, HeadSql, new object[] { id, kind.BizObjectId });
            if (head == null)
            {
                throw new BridgeException(404, "not_found", "单据不存在");
            }
            List<Dictionary<string, object>> lines = Rows.Query(ctx.Conn, LineSql, new object[] { id }, LineCap + 1);
            if (lines == null)
            {
                lines = new List<Dictionary<string, object>>();
            }
            bool truncated = lines.Count > LineCap;
            while (lines.Count > LineCap)
            {
                lines.RemoveAt(lines.Count - 1);
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["type"] = kind.Name;
            body["id"] = id;
            body["code"] = CoRows.Col(head, kind.CodeColumn);
            body["head"] = head;
            body["lines"] = lines;
            body["state"] = StateOf(head, kind);
            body["source"] = SourceOf(head, kind);
            if (truncated)
            {
                body["lines_truncated"] = true;
            }
            return ApiResult.Ok(body);
        }

        // 已审核：审核人和审核日期都非空（同 QmRead）。报检单没有关闭。
        static Dictionary<string, object> StateOf(Dictionary<string, object> head, VoucherKind kind)
        {
            string verifier = CoRows.Col(head, kind.VerifierColumn);
            string at = CoRows.Col(head, kind.VerifyDateColumn);
            Dictionary<string, object> state = new Dictionary<string, object>();
            state["verified"] = verifier.Length > 0 && at.Length > 0;
            state["verifier"] = verifier;
            state["verified_at"] = at;
            return state;
        }

        // 来源单据：类型按报检单种类固定（来料 → 到货单，产品 → 生产订单），id / code 取表头。没有来源 id 时为 null。
        // 其他报检单（QM11）没有来源单据，恒为 null。
        static object SourceOf(Dictionary<string, object> head, VoucherKind kind)
        {
            int sourceId = CoRows.AsId(CoRows.Col(head, "CSOURCEID"));
            if (sourceId <= 0 || kind.Name == "qm_other_inspect")
            {
                return null;
            }
            Dictionary<string, object> source = new Dictionary<string, object>();
            source["type"] = kind.Name == "qm_incoming_inspect" ? "arrival" : "production_order";
            source["id"] = sourceId;
            source["code"] = CoRows.Col(head, "CSOURCECODE");
            return source;
        }
    }
}
