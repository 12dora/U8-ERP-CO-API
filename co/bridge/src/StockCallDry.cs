using System;
using System.Collections.Generic;

namespace U8Co
{
    // 写预演（DryRun，回滚模式）：RunAt 在 After 核对之后、CommitSeen 之前调 DryAt，事务还在，
    // 在请求连接上登记本次新建 / 生成的单据。CommitSeen 的预演钩子据此生成预览并回滚（CoTrans）。
    internal static partial class StockCall
    {
        static void DryAt(WorkContext ctx, StockAt at)
        {
            if (!DryRun.Active)
            {
                return;
            }
            if (at.Dry != null)
            {
                at.Dry(ctx.Conn);
                return;
            }
            if (at.Method == "Insert")
            {
                DryInsert(ctx, at.Args);
                return;
            }
            // 调拨单、形态转换单审核（12 参 Verify）：字典里是审核生成的其他出入库单。
            if (at.Method == "Verify" && at.Args.Length == 12)
            {
                DryGenerated(ctx, at.Args[11]);
            }
        }

        // Insert 的新主键在引用槽 6（vouchid）；类型按 args[0] 的库存单据类型码找。
        static void DryInsert(WorkContext ctx, object[] args)
        {
            VoucherKind kind = KindOfSt(Values.Text(args[0]).Trim());
            int id = NewId(ctx, args[6]);
            if (kind == null || id <= 0)
            {
                DryRun.Set("new_id_unknown", true);
                return;
            }
            DryRun.Created(kind, id);
        }

        static void DryGenerated(WorkContext ctx, object dict)
        {
            List<Dictionary<string, object>> made;
            try
            {
                made = Generated(ctx, dict);
            }
            catch (Exception ex)
            {
                // 预演只登记预览；读不出生成的单据不影响回滚，记在 detail 里。
                CoRows.Note(ctx.Item, "dry generated " + ex.Message);
                DryRun.Set("generated_unknown", true);
                return;
            }
            for (int i = 0; i < made.Count; i++)
            {
                VoucherKind kind = Kinds.Find(Values.Text(made[i]["type"]));
                if (kind != null)
                {
                    DryRun.Created(kind, CoRows.AsId(made[i]["id"]));
                }
            }
        }

        // 库存单据类型码（01、08、09、10、11、12、15、18、32、62）在库存类型里唯一。
        internal static VoucherKind KindOfSt(string stType)
        {
            if (stType == null || stType.Length == 0)
            {
                return null;
            }
            foreach (VoucherKind kind in Kinds.All())
            {
                if (kind.Family == "st" && kind.StType == stType)
                {
                    return kind;
                }
            }
            return null;
        }
    }
}
