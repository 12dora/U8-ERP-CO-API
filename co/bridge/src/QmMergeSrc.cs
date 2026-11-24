using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 合并检验的一条来源：报检单表头、表体行（QmSql.InsHeadSql / InsLineSql 的列）和本次数量。
    internal sealed class QmMergeSource
    {
        public QmLineAsk Ask;
        public int InspectId;
        public Dictionary<string, object> Head;
        public Dictionary<string, object> Line;
        // PeekSql 的整行：审核、分拣标志、件数 Num 和合并规则比较的列（QmMergeRules）。
        public Dictionary<string, object> Peek;
        // 件数（报检行有辅计量时）：检验、合格、让步、不良。整行检验时检验件数取报检行自己的 FNUM，合格件数 = 检验 − 让步 − 不良。
        public decimal Num;
        public decimal RegNum;
        public decimal ConNum;
        public decimal DisNum;
    }

    // 合并检验的全部来源（按请求顺序）与表头取用的那条（AUTOID 最大的一条，同 U8 多数情况）。
    internal sealed class QmMergeSet
    {
        public readonly List<QmMergeSource> Sources = new List<QmMergeSource>();
        public QmMergeSource Lead;
        // 合并规则（QmMergeRules，缺省自由项）。
        public List<QmMergeRule> Rules = new List<QmMergeRule>();
        // 表头 CPROORDERCODE：表头取用的来源在前，其余按请求顺序，生产订单号去重后逗号连接（U8 合并检验同样如此），不超过列宽。
        public string MoCodes = "";
    }

    // 合并检验的来源校验（同 U8 合并检验的规则，docs/u8-notes.md「合并检验」）：每条来源是已审核产品报检单（QM02）的表体行，
    // 未生成过检验单（累计检验数量为 0，U8「累计检验数量大于零的单据已生成过检验单」）、不是分拣后的报检行（BMERGECHECKFLAG=1，
    // U8「不能和普通报检单合并检验」）；各来源的存货、辅计量单位（及换算率）、批号、检验方式、检验类型必须相同（U8 缺省合并依据），
    // 另按请求的合并规则（QmMergeRules，缺省 0 自由项）比较对应的列。
    internal static class QmMergeSrc
    {
        const int MoCodesMax = 255;
        const string PeekSql = "select convert(varchar(20), b.ID) as InspectId, h.CVOUCHTYPE, h.CVERIFIER,"
            + " convert(varchar(5), isnull(b.BMERGECHECKFLAG,0)) as Sorted, convert(varchar(40), isnull(b.FNUM,0)) as Num,"
            + " b.CFREE1, b.CFREE2, b.CFREE3, b.CFREE4, b.CFREE5, b.CFREE6, b.CFREE7, b.CFREE8, b.CFREE9, b.CFREE10,"
            + " h.CDEPCODE as RDEP, b.CWHCODE as RWH, b.CCONTRACTCODE as RCONTRACT, b.CPROORDERCODE as RMO, h.CCUSCODE as RCUS,"
            + " b.CPROBATCH as RPROBATCH, b.CITEMCLASS as RITEMCLASS, b.CITEMCODE as RITEMCODE"
            + " from QMINSPECTVOUCHERS b join QMINSPECTVOUCHER h on h.ID=b.ID where b.AUTOID=?";
        static readonly string[] SameLine = new string[] { "CINVCODE", "CUNITID", "Rate", "CBATCH", "TestStyle" };

        public static QmChkJob Load(WorkContext ctx, QmSpec spec, QmAsk ask, List<QmMergeLine> lines,
            List<QmMergeRule> rules)
        {
            QmMergeSet set = new QmMergeSet();
            set.Rules = rules;
            for (int i = 0; i < lines.Count; i++)
            {
                set.Sources.Add(Source(ctx.Conn, spec, lines[i]));
            }
            Same(set);
            set.Lead = Lead(set);
            set.MoCodes = MoCodes(set);
            QmChkJob job = QmChkSrc.New(ctx, spec, ask, Total(set), set.Lead.InspectId);
            job.Head = set.Lead.Head;
            job.Line = set.Lead.Line;
            job.Merge = set;
            QmChkSrc.Finish(job);
            return job;
        }

        static QmMergeSource Source(object conn, QmSpec spec, QmMergeLine asked)
        {
            string at = "报检单行 " + asked.Id.ToString(CultureInfo.InvariantCulture);
            Dictionary<string, object> peek = QmSql.One(conn, PeekSql, asked.Id);
            if (peek == null || CoRows.Col(peek, "CVOUCHTYPE") != "QM02")
            {
                throw new BridgeException(400, "bad_request", "产品报检单表体行不存在：" + asked.Id.ToString(CultureInfo.InvariantCulture));
            }
            if (CoRows.Col(peek, "CVERIFIER").Length == 0)
            {
                throw new BridgeException(409, "state_mismatch", at + " 所在的报检单未审核");
            }
            if (CoRows.Col(peek, "Sorted") == "1")
            {
                throw new BridgeException(409, "state_mismatch", at + " 是分拣后的报检行，不能和普通报检单合并检验");
            }
            QmMergeSource src = new QmMergeSource();
            src.Peek = peek;
            src.InspectId = CoRows.AsId(CoRows.Col(peek, "InspectId"));
            src.Head = QmSql.One(conn, QmSql.InsHeadSql, src.InspectId, "QM02");
            src.Line = QmSql.One(conn, QmSql.InsLineSql, asked.Id, src.InspectId);
            if (src.Head == null || src.Line == null)
            {
                throw new BridgeException(409, "state_mismatch", at + " 读取失败，请重试");
            }
            decimal total = QmSql.Dec(CoRows.Col(src.Line, "Total"));
            if (QmSql.Dec(CoRows.Col(src.Line, "Used")) != 0m)
            {
                throw new BridgeException(409, "state_mismatch", at + " 已生成过检验单，不能合并检验");
            }
            if (total <= 0m)
            {
                throw new BridgeException(409, "state_mismatch", at + " 没有可检验数量");
            }
            decimal qty = asked.Whole ? total : asked.Qty;
            QmMath.RequireLeft(qty, total, at);
            src.Ask = QmMergeReq.Split(asked, qty);
            PiecesOf(src, total, QmSql.Dec(CoRows.Col(peek, "Num")), QmSql.Dec(CoRows.Col(src.Line, "Rate")));
            return src;
        }

        // 存货、辅计量单位、批号、检验方式（行）和检验类型（报检单表头）必须相同。
        static void Same(QmMergeSet set)
        {
            QmMergeSource first = set.Sources[0];
            for (int i = 1; i < set.Sources.Count; i++)
            {
                QmMergeSource src = set.Sources[i];
                for (int k = 0; k < SameLine.Length; k++)
                {
                    if (Col(src.Line, SameLine[k]) != Col(first.Line, SameLine[k]))
                    {
                        throw Mixed(src, SameLine[k]);
                    }
                }
                if (Col(src.Head, "CCHECKTYPECODE") != Col(first.Head, "CCHECKTYPECODE"))
                {
                    throw Mixed(src, "CCHECKTYPECODE");
                }
                string rule = QmMergeRules.Mismatch(set.Rules, first.Peek, src.Peek);
                if (rule.Length > 0)
                {
                    throw MixedName(src, rule);
                }
            }
        }

        // 件数：有辅计量（单位和换算率）时按换算率折算；整行检验取报检行自己的件数，合格件数取差额，各行之和就是表头件数。
        internal static void PiecesOf(QmMergeSource src, decimal total, decimal lineNum, decimal rate)
        {
            if (CoRows.Col(src.Line, "CUNITID").Length == 0 || rate <= 0m)
            {
                return;
            }
            QmLineAsk a = src.Ask;
            src.Num = a.Qty == total && lineNum > 0m ? lineNum : QmChkDom.Pieces(a.Qty, rate);
            src.ConNum = QmChkDom.Pieces(a.Con, rate);
            src.DisNum = QmChkDom.Pieces(a.Dis, rate);
            src.RegNum = src.Num - src.ConNum - src.DisNum;
        }

        static BridgeException Mixed(QmMergeSource src, string col)
        {
            string name = col == "CINVCODE" ? "存货" : col == "CUNITID" ? "辅计量单位" : col == "Rate" ? "换算率"
                : col == "CBATCH" ? "批号" : col == "TestStyle" ? "检验方式" : "检验类型";
            return MixedName(src, name);
        }

        static BridgeException MixedName(QmMergeSource src, string name)
        {
            return new BridgeException(409, "state_mismatch", "合并检验的来源" + name + "不一致：报检单行 "
                + src.Ask.SourceLineId.ToString(CultureInfo.InvariantCulture));
        }

        static string Col(Dictionary<string, object> row, string name)
        {
            return CoRows.Col(row, name).Trim();
        }

        static QmMergeSource Lead(QmMergeSet set)
        {
            QmMergeSource lead = set.Sources[0];
            for (int i = 1; i < set.Sources.Count; i++)
            {
                if (set.Sources[i].Ask.SourceLineId > lead.Ask.SourceLineId)
                {
                    lead = set.Sources[i];
                }
            }
            return lead;
        }

        // 各来源数量之和（表头的检验、合格、让步、不良数量）；SourceLineId 是表头取用的来源。
        static QmLineAsk Total(QmMergeSet set)
        {
            QmLineAsk sum = new QmLineAsk();
            sum.SourceLineId = set.Lead.Ask.SourceLineId;
            for (int i = 0; i < set.Sources.Count; i++)
            {
                QmLineAsk a = set.Sources[i].Ask;
                sum.Qty += a.Qty;
                sum.Reg += a.Reg;
                sum.Con += a.Con;
                sum.Dis += a.Dis;
            }
            return sum;
        }

        internal static string MoCodes(QmMergeSet set)
        {
            List<string> codes = new List<string>();
            List<QmMergeSource> order = new List<QmMergeSource>(set.Sources);
            if (set.Lead != null && order.Remove(set.Lead))
            {
                order.Insert(0, set.Lead);
            }
            int length = 0;
            for (int i = 0; i < order.Count; i++)
            {
                string code = Col(order[i].Line, "CPROORDERCODE");
                if (code.Length == 0 || codes.Contains(code))
                {
                    continue;
                }
                int add = code.Length + (codes.Count > 0 ? 1 : 0);
                if (length + add > MoCodesMax)
                {
                    break;
                }
                codes.Add(code);
                length += add;
            }
            return string.Join(",", codes.ToArray());
        }

        // 数据权限：每条来源一行（部门取检验部门，供应商、存货、仓库取报检单）。
        public static List<Dictionary<string, object>> PermRows(QmChkJob job)
        {
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            for (int i = 0; i < job.Merge.Sources.Count; i++)
            {
                QmMergeSource src = job.Merge.Sources[i];
                Dictionary<string, object> row = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
                row["CVENCODE"] = CoRows.Col(src.Head, "CVENCODE");
                row["CDEPCODE"] = job.DepCode;
                row["CINVCODE"] = CoRows.Col(src.Line, "CINVCODE");
                row["CWHCODE"] = CoRows.Col(src.Line, "CWHCODE");
                rows.Add(row);
            }
            return rows;
        }

        // 各来源行的累计检验数量之和（保存前后对比，判断 U8 是否已回写）。
        internal static decimal Used(object conn, QmMergeSet set)
        {
            decimal sum = 0m;
            for (int i = 0; i < set.Sources.Count; i++)
            {
                sum += QmSql.Dec(QmSql.Scalar(conn, QmSql.InsUsedSql, set.Sources[i].Ask.SourceLineId));
            }
            return sum;
        }
    }
}
