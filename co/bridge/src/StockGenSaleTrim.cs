using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 销售出库生成的第 2 步（StockGenSalePart）：把 MakeOutVouch 已提交的出库单改回应出数量。
    // 实测的改单路径（u8-notes §8）：USERPCO Load 在事务外（它用 U8 自己的连接，事务里读不到新单、还会互锁），
    // 然后在新事务里 Update("32") 改数量 / editprop=D 删行，一行都不要的整张 Delete；提交前核对发货行 fOutQuantity。
    internal static partial class StockGen
    {
        sealed class OutPart
        {
            public WorkContext Ctx;
            public VoucherKind Kind;
            public int DlId;
            public int Floor;
            public bool Whole;
            public Dictionary<int, decimal[]> Before;
            public Dictionary<int, decimal> Want;
            public List<int> Made;
            // 请求带了批号 / 货位 / 拆行时非空，数量改好之后再写（StockGenSaleBatchApply）。
            public OutBatch Batch;
            Dictionary<int, decimal> target;
            List<TrimPlan> plans;
            object trimCo;
            object undoCo;

            // 返回保留下来的出库单 id（升序）。改回失败一律补偿（Undo）。
            public List<int> Settle()
            {
                try
                {
                    List<int> kept = NeedTrim(MadeOf(Ctx.Conn, Made)) ? Trim() : Made;
                    if (Batch != null)
                    {
                        Batch.Apply(this, kept);
                    }
                    return kept;
                }
                catch (DryRunDone)
                {
                    throw;
                }
                catch (Exception ex)
                {
                    throw Undo(ex);
                }
            }

            // 应出 = min(生成, 请求或剩余)。部分出库时生成不足请求数量就失败；整单时照旧接受 U8 生成的数量。
            bool NeedTrim(Dictionary<int, decimal> made)
            {
                target = new Dictionary<int, decimal>();
                bool excess = false;
                foreach (KeyValuePair<int, decimal> kv in made)
                {
                    decimal want;
                    Want.TryGetValue(kv.Key, out want);
                    excess = excess || kv.Value - want > 0.000001m;
                    target[kv.Key] = kv.Value < want ? kv.Value : want;
                }
                if (Whole)
                {
                    return excess;
                }
                foreach (KeyValuePair<int, decimal> kv in Want)
                {
                    decimal got;
                    made.TryGetValue(kv.Key, out got);
                    if (kv.Value - got > 0.000001m)
                    {
                        throw new BridgeException(409, "u8_rejected", "U8 生成的销售出库单不足请求数量");
                    }
                }
                return excess;
            }

            List<int> Trim()
            {
                plans = new List<TrimPlan>();
                try
                {
                    trimCo = StockCall.OpenCo(Ctx);
                    Dictionary<int, decimal> left = new Dictionary<int, decimal>(target);
                    for (int i = 0; i < Made.Count; i++)
                    {
                        TrimPlan plan = new TrimPlan();
                        plan.Id = Made[i];
                        plans.Add(plan);
                        LoadOne(plan, left);
                    }
                    foreach (decimal rest in left.Values)
                    {
                        if (rest > 0.000001m)
                        {
                            throw new BridgeException(409, "u8_rejected", "U8 生成的销售出库单与回读数量不符");
                        }
                    }
                    InTx(Ctx, ApplyTrim);
                    return KeptOf(plans);
                }
                finally
                {
                    ReleasePlans(plans);
                    ComUtil.Final(trimCo);
                    trimCo = null;
                }
            }

            // 事务外 Load（已提交的新单），按 left 标记。要改数量的单据有货位记录时不改（Update 改货位未实测），走补偿。
            void LoadOne(TrimPlan plan, Dictionary<int, decimal> left)
            {
                StockLoaded loaded = new StockLoaded();
                plan.Loaded = loaded;
                loaded.Head = Rows.NewDom();
                loaded.Body = Rows.NewDom();
                loaded.Pos = Rows.NewDom();
                StockCall.CallLoad(Ctx, trimCo, Kind, plan.Id, loaded);
                if (loaded.Pos == null)
                {
                    loaded.Pos = Rows.NewDom();
                }
                plan.State = Mark(loaded, left);
                if (plan.State == 2 && StockPosGuard.BinnedLines(Ctx.Conn, Kind, plan.Id).Count > 0)
                {
                    throw new BridgeException(409, "u8_rejected", "生成的销售出库单有货位记录，桥暂不支持按数量调整");
                }
            }

            // 新事务：先改后删，提交前核对每个发货行 fOutQuantity = 基线 + 应出。
            void ApplyTrim(object conn)
            {
                for (int i = 0; i < plans.Count; i++)
                {
                    if (plans[i].State == 2)
                    {
                        UpdateIn(plans[i]);
                    }
                }
                for (int i = 0; i < plans.Count; i++)
                {
                    if (plans[i].State == 0)
                    {
                        DropIn(Ctx, trimCo, Kind, plans[i].Id);
                    }
                }
                StockCall.AfterCheck(Ctx, CheckTarget, Label());
            }

            void UpdateIn(TrimPlan plan)
            {
                object msg = Rows.NewDom();
                try
                {
                    int[] refs;
                    object[] args = StockCall.UpdateArgs(Kind, StockCall.Forms(plan.Loaded.Head, plan.Loaded.Body,
                        plan.Loaded.Pos, Ctx.Conn, msg), out refs);
                    CallIn(Ctx, trimCo, StockCall.AtFor("Update", args, refs, null));
                }
                finally
                {
                    ComUtil.Final(msg);
                }
            }

            void CheckTarget(object conn)
            {
                CheckLines(conn, target, "的累计出库数量与请求不符");
            }

            // 补偿：删掉第 1 步生成、现在还在的出库单，核对 fOutQuantity 回到基线。成功 409（带原因），失败 504。
            BridgeException Undo(Exception why)
            {
                CoRows.Note(Ctx.Item, "销售出库调整失败，撤回生成的单据 " + IdList(Made) + "：" + why.Message);
                try
                {
                    undoCo = StockCall.OpenCo(Ctx);
                    InTx(Ctx, ApplyUndo);
                }
                catch (Exception ex)
                {
                    CoRows.Note(Ctx.Item, "撤回失败：" + ex.Message);
                    return new BridgeException(504, "outcome_unknown",
                        "销售出库单已生成，按数量调整失败且撤回失败，需要人工核对：出库单 id " + IdList(Made));
                }
                finally
                {
                    ComUtil.Final(undoCo);
                    undoCo = null;
                }
                return new BridgeException(409, "u8_rejected", "已撤回生成的销售出库单：" + Reason(why));
            }

            void ApplyUndo(object conn)
            {
                List<int> still = StillThere(conn);
                for (int i = 0; i < still.Count; i++)
                {
                    DropIn(Ctx, undoCo, Kind, still[i]);
                }
                StockCall.AfterCheck(Ctx, CheckBase, Label());
            }

            void CheckBase(object conn)
            {
                if (StillThere(conn).Count > 0)
                {
                    throw new BridgeException(409, "u8_rejected", "撤回后销售出库单仍在");
                }
                CheckLines(conn, new Dictionary<int, decimal>(), "的累计出库数量未回到生成前");
            }

            // 第 1 步生成的出库单里现在还在的。
            List<int> StillThere(object conn)
            {
                List<int> now = ListOut(conn, Kind, DlId.ToString(CultureInfo.InvariantCulture), Floor);
                List<int> still = new List<int>();
                for (int i = 0; i < now.Count; i++)
                {
                    if (Made.Contains(now[i]))
                    {
                        still.Add(now[i]);
                    }
                }
                return still;
            }

            // 每个基线发货行：fOutQuantity = 基线 + add（没有的行 +0）。
            void CheckLines(object conn, Dictionary<int, decimal> add, string what)
            {
                Dictionary<int, decimal[]> now = OpenLines(conn, DlId);
                foreach (KeyValuePair<int, decimal[]> kv in Before)
                {
                    decimal plus;
                    add.TryGetValue(kv.Key, out plus);
                    decimal[] cur;
                    if (!now.TryGetValue(kv.Key, out cur) || Math.Abs(cur[1] - kv.Value[1] - plus) > 0.000001m)
                    {
                        throw new BridgeException(409, "u8_rejected",
                            "发货单行 " + kv.Key.ToString(CultureInfo.InvariantCulture) + what);
                    }
                }
            }

            string Label()
            {
                return "发货单 " + DlId.ToString(CultureInfo.InvariantCulture) + " 出库单 " + IdList(Made);
            }

            // 补偿已成功时的原因：AfterCheck 的 500 / outcome_unknown 只说「调整后核对不符」（细节已记在审计备注），
            // 免得 409 的消息里出现「需要人工核对」。
            static string Reason(Exception why)
            {
                BridgeException bridge = why as BridgeException;
                if (bridge == null)
                {
                    return "内部错误";
                }
                if (bridge.Status == 500 || bridge.Code == "outcome_unknown")
                {
                    return "调整后核对不符";
                }
                return bridge.Message;
            }

            static List<int> KeptOf(List<TrimPlan> all)
            {
                List<int> kept = new List<int>();
                for (int i = 0; i < all.Count; i++)
                {
                    if (all[i].State != 0)
                    {
                        kept.Add(all[i].Id);
                    }
                }
                return kept;
            }

            static void ReleasePlans(List<TrimPlan> all)
            {
                for (int i = 0; all != null && i < all.Count; i++)
                {
                    StockLoaded loaded = all[i].Loaded;
                    if (loaded == null)
                    {
                        continue;
                    }
                    ComUtil.Final(loaded.Pos);
                    ComUtil.Final(loaded.Body);
                    ComUtil.Final(loaded.Head);
                }
            }

            // 0：没有保留的行；1：全部保留、数量不变；2：有改动。
            int Mark(StockLoaded loaded, Dictionary<int, decimal> left)
            {
                List<object> heads = DomRows.RowsOf(loaded.Head);
                List<object> rows = DomRows.RowsOf(loaded.Body);
                try
                {
                    List<string> names = DomRows.Schema(loaded.Body);
                    int kept = 0;
                    bool changed = false;
                    for (int i = 0; i < rows.Count; i++)
                    {
                        string mark = MarkRow(loaded.Body, rows[i], left, names);
                        kept += mark == "D" ? 0 : 1;
                        changed = changed || mark.Length > 0;
                    }
                    if (kept == 0 || !changed)
                    {
                        return kept == 0 ? 0 : 1;
                    }
                    if (heads.Count == 0)
                    {
                        throw new BridgeException(409, "u8_rejected", "U8 生成的销售出库单没有表头");
                    }
                    StockDom.SetCell(loaded.Head, heads[0], "editprop", "M");
                    return 2;
                }
                finally
                {
                    ReleaseAll(heads);
                    ReleaseAll(rows);
                }
            }

            // 同一发货行拆成多行时按顺序分配应出数量。改了数量的行重算辅数量（按换算率）和金额（有单位成本时）。
            string MarkRow(object dom, object row, Dictionary<int, decimal> left, List<string> names)
            {
                int line = CoRows.AsId(DomRows.Get(row, "iDLsID"));
                decimal rest;
                if (!left.TryGetValue(line, out rest) || rest <= 0m)
                {
                    StockDom.SetCell(dom, row, "editprop", "D", names);
                    return "D";
                }
                decimal have;
                if (!StockUnits.Dec(DomRows.Get(row, "iQuantity"), out have) || have <= 0m)
                {
                    throw new BridgeException(409, "u8_rejected", "U8 生成的销售出库单行数量无法识别");
                }
                decimal take = have < rest ? have : rest;
                left[line] = rest - take;
                if (take == have)
                {
                    StockDom.SetCell(dom, row, "editprop", "", names);
                    return "";
                }
                StockDom.SetCell(dom, row, "iQuantity", StockUnits.Price(take), names);
                StockDom.SetCell(dom, row, "editprop", "M", names);
                StockUnits.UnitJob job = new StockUnits.UnitJob();
                job.QtyName = "iQuantity";
                job.NumName = "iNum";
                job.Force = true;
                job.Schema = names;
                StockUnits.ApplyDom(Ctx.Conn, dom, row, job);
                StockUnits.FixPriceSent(dom, row, false, false, true, names);
                return "M";
            }
        }

        sealed class TrimPlan
        {
            public int Id;
            public StockLoaded Loaded;
            public int State;
        }
    }
}
