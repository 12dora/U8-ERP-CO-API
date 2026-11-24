using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 生产订单修改里调用方给的子件替代料（lines[i].allocations）。每项 {sort_seq, inv_code, substitutes}：按（行 line_id、子件行号、
    // 子件存货）定位子件（不收 AllocateId：U8 每次修改都换新）；substitutes 整组替换该子件现有的替代料，空数组清空；
    // 没列出的子件、没带 allocations 的行保留现有替代料。每项替代料的形状和存货规则同物料清单（SubstituteReq / SubstituteCheck），
    // 另收 defines；没给日期的写 NULL（生产订单子件没有起止日期可取）。这是调用方内容的复现写入，属第二级（TestAccountGate）。
    internal static class MoUpdateSubsReq
    {
        public const string Key = "allocations";
        public const int AllocsMax = 200;
        public const string TestOnly = "写入生产订单子件的替代料（allocations）只对配置为测试账套的账套开放";
        static readonly string[] AllocKeys = new string[] { "sort_seq", "inv_code", MoSubs.Key };

        // 带了 allocations 的请求只对测试账套开放（含预演）：登录前（RequestsSpecs）和入队后（MoUpdate）各查一次。
        internal static void RequireTier(WorkItem item, MoUpdateAsk ask)
        {
            if (ask.HasAllocs)
            {
                TestAccountGate.Require(item, TestOnly);
            }
        }

        // 登录前：解析一行的 allocations（1 到 200 项，同一行里（行号、存货）不重复）。
        internal static List<MoAllocEdit> Parse(object value)
        {
            IList items = value as IList;
            if (items == null)
            {
                throw BridgeException.BadField(Key, "allocations 必须是数组");
            }
            if (items.Count == 0 || items.Count > AllocsMax)
            {
                throw BridgeException.BadField(Key, "allocations 必须是 1 到 " + BomReq.Num(AllocsMax) + " 项");
            }
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            List<MoAllocEdit> list = new List<MoAllocEdit>();
            for (int i = 0; i < items.Count; i++)
            {
                try
                {
                    MoAllocEdit edit = Item(items[i]);
                    if (!seen.Add(BomReq.Num(edit.SortSeq) + "|" + MoAllocRow.Norm(edit.InvCode)))
                    {
                        throw new BridgeException(400, "bad_request", "子件重复：行号 " + BomReq.Num(edit.SortSeq) + "、存货 " + edit.InvCode);
                    }
                    list.Add(edit);
                }
                catch (BridgeException ex)
                {
                    throw FieldPath.Index(ex, Key, i);
                }
            }
            return list;
        }

        static MoAllocEdit Item(object raw)
        {
            Dictionary<string, object> item = raw as Dictionary<string, object>;
            if (item == null)
            {
                throw new BridgeException(400, "bad_request", "allocations 的每一项必须是对象");
            }
            BomReq.Only(item, AllocKeys);
            object seq = MfgReq.Raw(item, "sort_seq");
            if (seq == null)
            {
                throw BridgeException.BadField("sort_seq", "缺少字段 sort_seq");
            }
            object subs = MfgReq.Raw(item, MoSubs.Key);
            if (subs == null)
            {
                throw BridgeException.BadField(MoSubs.Key, "缺少字段 substitutes（空数组表示清空）");
            }
            MoAllocEdit edit = new MoAllocEdit();
            edit.SortSeq = BomReq.Int(seq, "sort_seq", 0, int.MaxValue);
            edit.InvCode = BomReq.Str(item, "inv_code", 60, true);
            edit.Subs = SubstituteReq.Parse(subs, MoSubs.Key, true);
            for (int k = 0; k < edit.Subs.Count; k++)
            {
                if (SubstituteReq.Reversed(edit.Subs[k]))
                {
                    throw FieldPath.Index(BridgeException.BadField("eff_begin", "eff_begin 不能晚于 eff_end"), MoSubs.Key, k);
                }
            }
            return edit;
        }

        // 登录后：每项对上快照里恰好一个子件（409），查替代存货（400），把目标替代料按 AllocateId 写进 ask.Subs。
        // today 是登录日期（存货停用日期按它判断，同物料清单）。
        internal static void Resolve(object conn, MoUpdateAsk ask, MoSnap snap, string today)
        {
            Dictionary<string, int> parts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < ask.Edits.Count; i++)
            {
                MoEdit edit = ask.Edits[i];
                if (edit.Allocs == null)
                {
                    continue;
                }
                MoLineRow line = snap.LineById(edit.LineId);
                if (line == null)
                {
                    throw new BridgeException(400, "bad_request", "明细行不存在：" + edit.LineId.ToString(CultureInfo.InvariantCulture));
                }
                for (int k = 0; k < edit.Allocs.Count; k++)
                {
                    MoAllocEdit a = edit.Allocs[k];
                    MoAllocRow row = Find(snap, line, a);
                    string at = At(line.SortSeq, row.InvCode, row.SortSeq);
                    List<int> ids = SubstituteCheck.Parts(conn, a.Subs, new string[] { row.InvCode, line.InvCode }, today, parts, at);
                    ask.Subs[row.AllocateId] = Rows(row.AllocateId, a.Subs, ids);
                }
            }
        }

        // （行、行号、存货）对上恰好一个子件；没有或不止一个 409（同 MoUpdateSql.Gate 的说法）。
        internal static MoAllocRow Find(MoSnap snap, MoLineRow line, MoAllocEdit a)
        {
            MoAllocRow hit = null;
            List<MoAllocRow> allocs = snap.AllocsOf(line.MoDId);
            for (int i = 0; i < allocs.Count; i++)
            {
                if (allocs[i].SortSeq != a.SortSeq || MoAllocRow.Norm(allocs[i].InvCode) != MoAllocRow.Norm(a.InvCode))
                {
                    continue;
                }
                if (hit != null)
                {
                    throw new BridgeException(409, "state_mismatch", "生产订单有行号、存货都相同的子件（" + a.InvCode + "），请在 U8 客户端修改");
                }
                hit = allocs[i];
            }
            if (hit == null)
            {
                throw new BridgeException(409, "state_mismatch", "第 " + BomReq.Num(line.SortSeq) + " 行没有行号 " + BomReq.Num(a.SortSeq)
                    + "、存货 " + a.InvCode + " 的子件");
            }
            return hit;
        }

        internal static string At(int lineSeq, string inv, int seq)
        {
            return "第 " + BomReq.Num(lineSeq) + " 行子件 " + inv + "（行号 " + BomReq.Num(seq) + "）";
        }

        // 请求的一组替代料 → 要写的行。各列文本与 MoSubs.Read 读出的同形（系数 6 位小数、日期 style 121、NULL 为 NullMark），
        // 写后按 MoSubs.SameSet 核对。纯逻辑，--selftest 覆盖。
        internal static List<MoSubRow> Rows(int allocateId, List<BomSub> subs, List<int> parts)
        {
            List<MoSubRow> rows = new List<MoSubRow>();
            for (int k = 0; k < subs.Count; k++)
            {
                BomSub sub = subs[k];
                MoSubRow row = new MoSubRow();
                row.AllocateId = allocateId;
                row.Sequence = sub.Seq;
                row.PartId = parts[k];
                row.InvCode = sub.InvCode;
                row.Cells["Factor"] = sub.Factor.ToString("0.000000", CultureInfo.InvariantCulture);
                row.Cells["EffBegDate"] = DateCell(sub.EffBeg);
                row.Cells["EffEndDate"] = DateCell(sub.EffEnd);
                row.Cells["ReplaceFlag"] = sub.Replace == 1 ? "1" : "0";
                for (int i = 0; i < SubstituteRows.Defines.Length; i++)
                {
                    string n = SubstituteRows.Defines[i][0];
                    string cell;
                    if (sub.Defines == null || !sub.Defines.TryGetValue(n, out cell))
                    {
                        cell = MoUpdateCols.NullMark;
                    }
                    row.Cells["Define" + n] = cell;
                    if (SubstituteRows.Defines[i][1] == "f")
                    {
                        row.Cells["Define" + n + MoSubs.ShownSuffix] = cell;
                    }
                }
                rows.Add(row);
            }
            return rows;
        }

        static string DateCell(string date)
        {
            return date.Length == 0 ? MoUpdateCols.NullMark : date + " 00:00:00.000";
        }
    }

    // 请求里的一个子件：（行号、存货）和目标替代料。
    internal sealed class MoAllocEdit
    {
        public int SortSeq;
        public string InvCode = "";
        public List<BomSub> Subs = new List<BomSub>();
    }
}
