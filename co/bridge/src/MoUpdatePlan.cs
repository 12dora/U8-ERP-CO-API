using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 生产订单修改的目标：快照上叠调用方的改动，得到每行改后的值和每个子件改后的数量。
    // MOrderUpdate 会清掉每一行已有的子件，再按送去的 Mom_MoAllocate 重插（docs/u8-notes.md §9），所以全部行、全部子件都要重送：
    // 没改数量的行子件原样；改了数量的行，每个子件的数量、件数按 U8 自己的算法重算（MoUpdateAlloc：变动 / 固定用量、
    // 子件损耗率、母件损耗率、返工订单）。行上有辅计量的按换算率重算行件数。
    internal static class MoUpdatePlan
    {
        public static MoPlan Build(MoUpdateAsk ask, MoSnap snap)
        {
            MoPlan plan = new MoPlan();
            plan.QtyDigits = snap.QtyDigits;
            plan.AuxDigits = snap.AuxDigits;
            Dictionary<int, MoEdit> edits = Index(ask, snap);
            for (int i = 0; i < snap.Lines.Count; i++)
            {
                MoLineRow row = snap.Lines[i];
                MoEdit edit;
                edits.TryGetValue(row.MoDId, out edit);
                MoTarget target = Target(row, edit, ask.HeadRemark, snap.QtyDigits);
                target.AuxQty = target.QtyChanged && row.AuxUnit.Length > 0 && row.ChangeRate > 0m
                    ? Round(target.Qty / row.ChangeRate, snap.AuxDigits) : -1m;
                plan.Lines.Add(target);
                plan.BySeq[row.SortSeq] = target;
                plan.Allocs[row.MoDId] = AllocTargets(snap.AllocsOf(row.MoDId), target, snap);
                CheckDue(target, plan.Allocs[row.MoDId]);
                plan.Changed = plan.Changed || target.Touched;
            }
            return plan;
        }

        // line_id 必须是本单的行；行号重复的订单 U8 按行号对不上，不改。
        static Dictionary<int, MoEdit> Index(MoUpdateAsk ask, MoSnap snap)
        {
            HashSet<int> seqs = new HashSet<int>();
            for (int i = 0; i < snap.Lines.Count; i++)
            {
                if (!seqs.Add(snap.Lines[i].SortSeq))
                {
                    throw new BridgeException(409, "state_mismatch", "生产订单有重复的行号，请在 U8 客户端修改");
                }
            }
            Dictionary<int, MoEdit> map = new Dictionary<int, MoEdit>();
            for (int i = 0; i < ask.Edits.Count; i++)
            {
                MoEdit edit = ask.Edits[i];
                if (snap.LineById(edit.LineId) == null)
                {
                    throw new BridgeException(400, "bad_request", "明细行不存在：" + edit.LineId.ToString(CultureInfo.InvariantCulture));
                }
                map[edit.LineId] = edit;
            }
            return map;
        }

        static MoTarget Target(MoLineRow row, MoEdit edit, string headRemark, int digits)
        {
            MoTarget t = new MoTarget();
            t.Row = row;
            t.Qty = row.Qty;
            t.Start = row.Start;
            t.Due = row.Due;
            t.Remark = row.Remark;
            t.Defines = new Dictionary<string, string>(row.Defines, StringComparer.Ordinal);
            if (headRemark != null)
            {
                t.Remark = headRemark;
            }
            if (edit != null)
            {
                Apply(t, edit, digits);
            }
            if (string.CompareOrdinal(t.Due, t.Start) < 0)
            {
                throw Bad(row, "due_date 不能早于 start_date");
            }
            t.Touched = t.QtyChanged || t.Start != row.Start || t.Due != row.Due || t.Remark != row.Remark
                || DefinesChanged(t, row);
            return t;
        }

        static void Apply(MoTarget t, MoEdit edit, int digits)
        {
            MoLineRow row = t.Row;
            if (edit.InvCode != null && !string.Equals(edit.InvCode.Trim(), row.InvCode.Trim(), StringComparison.OrdinalIgnoreCase))
            {
                throw Bad(row, "不能修改存货编码（U8 不允许换行上的存货），请删除订单后重新新增");
            }
            if (edit.HasQty)
            {
                if (decimal.Round(edit.Qty, digits) != edit.Qty)
                {
                    throw Bad(row, "qty 最多 " + digits.ToString(CultureInfo.InvariantCulture) + " 位小数（U8 存货数量小数位）");
                }
                t.Qty = edit.Qty;
                t.QtyChanged = edit.Qty != row.Qty;
            }
            t.Due = edit.Due ?? t.Due;
            t.Remark = edit.Remark ?? t.Remark;
            foreach (KeyValuePair<string, string> kv in edit.Defines)
            {
                t.Defines[kv.Key] = kv.Value;
            }
        }

        static bool DefinesChanged(MoTarget t, MoLineRow row)
        {
            foreach (KeyValuePair<string, string> kv in t.Defines)
            {
                string old;
                row.Defines.TryGetValue(kv.Key, out old);
                if ((old ?? "") != (kv.Value ?? ""))
                {
                    return true;
                }
            }
            return false;
        }

        static List<MoAllocTarget> AllocTargets(List<MoAllocRow> allocs, MoTarget line, MoSnap snap)
        {
            List<MoAllocTarget> list = new List<MoAllocTarget>();
            for (int i = 0; i < allocs.Count; i++)
            {
                MoAllocRow a = allocs[i];
                MoAllocTarget t = new MoAllocTarget();
                t.Row = a;
                t.Qty = a.Qty;
                t.AuxQty = -1m;
                // UpperMoQty：U8 客户端改数量时保持原值（账套里有 160 行与现行数量不同、子件却按现行数量），桥写回原值。
                t.Upper = a.UpperMoQty;
                if (line.QtyChanged)
                {
                    MoUpdateAlloc.Scale(t, line.Row, line.Qty, snap.QtyDigits, snap.AuxDigits);
                }
                list.Add(t);
            }
            return list;
        }

        // 产出品子件的需求日期在 U8 里跟完工日期走（UpdEndDemDate），MOrderUpdate 重建子件时沿用旧日期（实测）：这种行不改完工日期。
        static void CheckDue(MoTarget t, List<MoAllocTarget> allocs)
        {
            if (t.Due == t.Row.Due)
            {
                return;
            }
            for (int i = 0; i < allocs.Count; i++)
            {
                if (MoUpdateSql.Dec(allocs[i].Row.Get("ByproductFlag")) != 0m)
                {
                    throw new BridgeException(409, "state_mismatch", "第 " + t.Row.SortSeq.ToString(CultureInfo.InvariantCulture)
                        + " 行有产出品子件，暂不支持修改完工日期（U8 重建子件时不重算需求日期）");
                }
            }
        }

        internal static decimal Round(decimal value, int digits)
        {
            return decimal.Round(value, digits, MidpointRounding.AwayFromZero);
        }

        static BridgeException Bad(MoLineRow row, string text)
        {
            return new BridgeException(400, "bad_request",
                "第 " + row.SortSeq.ToString(CultureInfo.InvariantCulture) + " 行：" + text);
        }
    }

    internal sealed class MoPlan
    {
        public List<MoTarget> Lines = new List<MoTarget>();
        public Dictionary<int, MoTarget> BySeq = new Dictionary<int, MoTarget>();
        // 行 MoDId → 该行全部子件的目标（顺序同快照）。
        public Dictionary<int, List<MoAllocTarget>> Allocs = new Dictionary<int, List<MoAllocTarget>>();
        public bool Changed;
        public int QtyDigits = 6;
        public int AuxDigits = 6;
    }

    internal sealed class MoTarget
    {
        public MoLineRow Row;
        public decimal Qty;
        public bool QtyChanged;
        // 行件数；-1 表示不改（行没有辅计量或数量没变）。
        public decimal AuxQty = -1m;
        public string Start;
        public string Due;
        public string Remark;
        public Dictionary<string, string> Defines;
        public bool Touched;
    }

    internal sealed class MoAllocTarget
    {
        public MoAllocRow Row;
        public decimal Qty;
        // 子件件数；-1 表示不改。
        public decimal AuxQty;
        // UpperMoQty 目标（原文或按比例的值，空串表示 NULL）。
        public string Upper = "";
    }
}
