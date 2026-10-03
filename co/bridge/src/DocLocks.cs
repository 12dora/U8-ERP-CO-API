using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 单据锁只用于排程：键被占用的任务留在队列里，不会有线程拿着键等待。
    // 实例不自带锁，调用方持有所在线程池的锁。
    internal sealed class DocLocks
    {
        const string CreatePath = "/u8co/v1/vouchers/create";
        const string GeneratePath = "/u8co/v1/vouchers/generate";
        static readonly string[] None = new string[0];
        readonly HashSet<string> _held = new HashSet<string>(StringComparer.Ordinal);

        public bool Free(string[] keys)
        {
            if (keys == null)
            {
                return true;
            }
            for (int i = 0; i < keys.Length; i++)
            {
                if (_held.Contains(keys[i]))
                {
                    return false;
                }
            }
            return true;
        }

        public void Hold(string[] keys)
        {
            if (keys == null)
            {
                return;
            }
            for (int i = 0; i < keys.Length; i++)
            {
                _held.Add(keys[i]);
            }
        }

        public void Release(string[] keys)
        {
            if (keys == null)
            {
                return;
            }
            for (int i = 0; i < keys.Length; i++)
            {
                _held.Remove(keys[i]);
            }
        }

        // 有 id 的路由锁 "<kind>:<id>"；新增锁 "new:<kind>"，同类单据的编号串行；
        // 生单的 id 是来源单据，锁来源 "<来源类型>:<id>"，并和新增一样锁 "new:<kind>"。
        // 总账凭证锁 "gl:<年>:<期间>:<凭证类别>:<凭证号>"，新增锁 "new:gl:<凭证类别>"；档案锁 "arc:<档案>:<编码>"；列表不锁。
        // 开了 serializeWrites 时，写入另持闸门键 "u8:write"，按账套时 "u8:write:<账套>"（WriteGate.KeyOf）。
        public static string[] KeysOf(WorkItem item)
        {
            if (item == null)
            {
                return None;
            }
            return WriteGate.Apply(item, DocKeys(item));
        }

        static string[] DocKeys(WorkItem item)
        {
            string[] route = RouteKeys(item);
            if (route != null)
            {
                return route;
            }
            if (item.Kind == WorkItem.SaleKind)
            {
                return new string[] { Key("sale_order", item.Id) };
            }
            if (item.Kind == WorkItem.DispatchKind)
            {
                return new string[] { Key("dispatch", item.Id) };
            }
            if (item.Type == null || item.Type.Name == null)
            {
                return None;
            }
            string kind = item.Type.Name;
            if (item.Path == CreatePath)
            {
                // 先开票发票另锁 new:dispatch，到货单另锁 new:purchase_return（SrcLess.CreateLocks）。
                return SrcLess.CreateLocks(kind);
            }
            if (item.Path == GeneratePath)
            {
                return GenerateKeys(item, kind);
            }
            if (!item.HasId)
            {
                return None;
            }
            return IdKeys(item, kind);
        }

        // 质量单据删除：来源单据的 id 要查库才知道，锁不到 "arrival:<id>" 这类键；改为另锁同类生单的 "new:<kind>"，
        // 与同类单据的生单（持来源键和 "new:<kind>"）串行。报检单删除还持本单键，与参照它生成检验单、弃审串行。
        // 不良品处理单删除同样另锁 "new:<kind>"（U8 删除时回退检验单的 BREJFLAG）；其他报检单、其他检验单同理。
        // 采购结算单删除也另锁 "new:purchase_settle"：与参照发票结算串行（删除回退发票的结算日期）。
        static string[] IdKeys(WorkItem item, string kind)
        {
            if (item.Path == "/u8co/v1/vouchers/delete" && (QmSpec.Handles(item.Type) || QmRejSpec.Handles(item.Type)
                || QmOthSpec.Handles(item.Type) || PuSettleReq.Handles(item.Type)))
            {
                return new string[] { Key(kind, item.Id), "new:" + kind };
            }
            // 收付款单审核、弃审另锁 "note:<AR|AP>"、"arap:writeoff:<AR|AP>"：票据生成的收付款单弃审要查票据未处理，与票据登记删除、票据处理串行。
            if (item.Path == "/u8co/v1/vouchers/verify" && (kind == "ar_receipt" || kind == "ap_payment"))
            {
                string flag = kind == "ar_receipt" ? "AR" : "AP";
                return new string[] { Key(kind, item.Id), "note:" + flag, "arap:writeoff:" + flag };
            }
            return new string[] { Key(kind, item.Id) };
        }

        // 不按单据类型分的路由；返回 null 表示不是这类路由。
        static string[] RouteKeys(WorkItem item)
        {
            string path = item.Path ?? "";
            if (path.StartsWith(Requests.GlRoot, StringComparison.Ordinal))
            {
                return GlKeys(item, path);
            }
            if (path.StartsWith(Requests.ArcRoot, StringComparison.Ordinal))
            {
                return ArcKeys(item);
            }
            if (path == Requests.ListPath || path == Requests.StockPath)
            {
                return None;
            }
            // 单据批量读取：与单张 vouchers/load 一样逐张锁 "<类型>:<id>"（LoadManyReq.LockKeys）。
            if (path == LoadMany.Path)
            {
                return LoadManyReq.LockKeys(item);
            }
            // 取消应收冲应付 / 应付冲应收 / 并账：每个涉及的账 "arap:writeoff:<AR|AP>"、"arap:proc:<处理号>"（ArapProcCancelReq.LockKeys）。
            if (ArapProcCancelReq.IsPath(path))
            {
                return ArapProcCancelReq.LockKeys(item.Body);
            }
            // 处理制单："arap:proc:<批次号>"、"new:gl:<类别>"、"arap:voucher:<AR|AP>"（ArapProcVoucherReq.LockKeys）。
            if (path == ArapProcVoucherReq.Path)
            {
                return ArapProcVoucherReq.LockKeys(item.Body);
            }
            // 应收冲应付 / 应付冲应收："arap:writeoff:AP"、"arap:writeoff:AR"（两侧余额都动，ArapTransferReq.LockKeys）。
            if (path == Requests.TransferPath)
            {
                return ArapTransferReq.LockKeys(item.Body);
            }
            return ArapKeys(item, path);
        }

        // 应收应付的核销、取消核销、自动核销、制单、取消制单；返回 null 表示不是这类路由。
        static string[] ArapKeys(WorkItem item, string path)
        {
            // 核销：收付款单、各被核销单据，另加 "arap:writeoff:<AR|AP>"（ArapWriteoffReq.LockKeys）。
            if (path == Requests.WriteoffPath)
            {
                return ArapWriteoffReq.LockKeys(item.Body);
            }
            // 取消核销：只有 "arap:writeoff:<AR|AP>"（收付款单和单据入队前查不到，ArapUnwriteoffReq.LockKeys）。
            if (path == Requests.WriteoffCancelPath)
            {
                return ArapUnwriteoffReq.LockKeys(item.Body);
            }
            // 自动核销：只有 "arap:writeoff:<AR|AP>"（要核销的单据入队前查不到，ArapAutoWriteoffReq.LockKeys）。
            if (path == Requests.WriteoffAutoPath)
            {
                return ArapAutoWriteoffReq.LockKeys(item.Body);
            }
            // 制单：单据键、"new:gl:<类别>"（与总账新增共用，凭证编号串行）、"arap:voucher:<AR|AP>"；
            // 取消制单只有 "arap:voucher:<AR|AP>"（ArapVoucherReq.LockKeys）。
            if (path == Requests.ArapVoucherPath || path == Requests.ArapVoucherDropPath)
            {
                return ArapVoucherReq.LockKeys(item.Body, path == Requests.ArapVoucherDropPath);
            }
            // 期初记账 / 取消记账：同一模块串行 "opening:<模块>"（OpeningPostReq.LockKeys）。
            if (path == OpeningPostReq.Path)
            {
                return OpeningPostReq.LockKeys(item.Body);
            }
            // 应收 / 应付期初单据：与 ar_bill / ap_bill 共用 "new:<类型>" / "<类型>:<id>"（OpeningsArapReq.LockKeys）。
            if (path == OpeningsArapReq.Path)
            {
                return OpeningsArapReq.LockKeys(item.Body);
            }
            // 月末结账 / 取消结账："period:<模块>"，through 锁全部模块（PeriodCloseReq.LockKeys）。
            if (path == PeriodCloseReq.Path)
            {
                return PeriodCloseReq.LockKeys(item.Body);
            }
            // 存货核算记账、期末处理："ia:<年度>-<期间>" 和 "period:ia"（IaReq.LockKeys）。
            if (IaReq.IsPath(path))
            {
                return IaReq.LockKeys(path, item.Body);
            }
            return MoreArapKeys(item, path);
        }

        // ArapKeys 的下一档：汇兑损益、并账；返回 null 表示不是这类路由。
        static string[] MoreArapKeys(WorkItem item, string path)
        {
            // 汇兑损益、取消汇兑损益："arap:writeoff:<AR|AP>"、"arap:exgain:<AR|AP>"（ArapExGainReq.LockKeys）。
            if (ArapExGainReq.IsPath(path))
            {
                return ArapExGainReq.LockKeys(path, item.Body);
            }
            // 坏账处理（发生、收回、计提）："arap:writeoff:AR"、"arap:bad:AR"（ArapBadReq.LockKeys）。
            if (ArapBadReq.IsPath(path))
            {
                return ArapBadReq.LockKeys(item.Body);
            }
            // 期间损益结转、自定义转账："gl:transfer:<年度>-<期间>"、"gl:post"、"period:gl"、常见类别的 "new:gl:<类别>"
            // （GlTransferReq.LockKeys）。
            if (GlTransferReq.IsPath(path))
            {
                return GlTransferReq.LockKeys(item.Body);
            }
            // 红票对冲："arap:writeoff:<AR|AP>"（ArapRedReq.LockKeysOf）。
            if (ArapRedReq.IsPath(path))
            {
                return ArapRedReq.LockKeysOf(path, item.Body);
            }
            // 票据处理："arap:writeoff:<AR|AP>"，背书两侧都锁（NotesProcReq.LockKeysOf）。
            if (NotesProcReq.IsPath(path))
            {
                return NotesProcReq.LockKeysOf(path, item.Body);
            }
            // 票据登记、删除："note:AR" 加 "new:ar_receipt" / "arap:writeoff:AR"（NotesRegReq.LockKeysOf）。
            if (NotesRegReq.IsPath(path))
            {
                return NotesRegReq.LockKeysOf(path, item.Body);
            }
            // 并账："arap:writeoff:<AR|AP>"（ArapMergeReq.LockKeysOf；不是并账路由返回 null）。
            return ArapMergeReq.LockKeysOf(path, item.Body);
        }

        static string[] GlKeys(WorkItem item, string path)
        {
            Dictionary<string, object> body = item.Body;
            if (path == Requests.GlRoot + "create")
            {
                Dictionary<string, object> head = Requests.Field(body, "head") as Dictionary<string, object>;
                return new string[] { "new:gl:" + Requests.KeyPart(Requests.Field(head, "sign")) };
            }
            // 记账：全局 "gl:post" 加每张凭证的键（GlPostParse.LockKeys）。取消记账同一把 "gl:post"，
            // 带了 vouchers 时再加这些凭证的键（不带时范围在事务里才读到，靠 SQL 锁）。
            if (path == Requests.GlRoot + "post" || path == GlUnpostReq.Path)
            {
                return GlPostParse.LockKeys(body, GlYear(item));
            }
            // 红字冲销：原凭证的键、新凭证编号 "new:gl:<类别>" 和 "gl:post"（GlReverseReq.LockKeys）。
            if (path == GlReverseReq.Path)
            {
                return GlReverseReq.LockKeys(body, GlYear(item));
            }
            if (body == null || !body.ContainsKey("no"))
            {
                return None;
            }
            return new string[]
            {
                "gl:" + GlYear(item) + ":" + Requests.KeyPart(Requests.Field(body, "period"))
                    + ":" + Requests.KeyPart(Requests.Field(body, "sign"))
                    + ":" + Requests.KeyPart(Requests.Field(body, "no"))
            };
        }

        // 会计年度取登录日期的年份，与总账处理一致（请求里的 year 是账套库年度）。
        static string GlYear(WorkItem item)
        {
            string date = item.Date ?? "";
            return date.Length >= 4 ? date.Substring(0, 4) : date;
        }

        // 档案编码在 U8 里不分大小写，键统一成大写。
        static string[] ArcKeys(WorkItem item)
        {
            Dictionary<string, object> body = item.Body;
            if (body == null || !body.ContainsKey("code"))
            {
                return None;
            }
            string archive = Requests.KeyPart(Requests.Field(body, "archive"));
            string code = Requests.KeyPart(Requests.Field(body, "code")).ToUpperInvariant();
            // 客户、供应商的银行账户和联系人另锁上级档案（ArcPartner.LockKeys）。
            string[] partner = ArcPartner.LockKeys(archive, code);
            string[] keys = partner != null ? partner : new string[] { "arc:" + archive + ":" + code };
            return ArcWrite(item.Path) ? WithArcGate(keys) : keys;
        }

        // 实测：几个 STA 工人同时做 EAI 档案写（部门修改、仓库删除、客户新增）时一起卡住约 3.5 分钟，
        // SQL Server 不报死锁（等在 U8 组件里），四个工人占满后其余请求排队超时。档案写入一律再持全局键 "arc:write" 串行。
        static bool ArcWrite(string path)
        {
            return path != null && (path.EndsWith("/create", StringComparison.Ordinal)
                || path.EndsWith("/update", StringComparison.Ordinal) || path.EndsWith("/delete", StringComparison.Ordinal));
        }

        static string[] WithArcGate(string[] keys)
        {
            string[] all = new string[keys.Length + 1];
            Array.Copy(keys, all, keys.Length);
            all[keys.Length] = "arc:write";
            return all;
        }

        // 退货单与发货单同表、共用 cDLCode 编号：生单时另锁 "new:dispatch"，与发货单生单串行。
        static string[] GenerateKeys(WorkItem item, string kind)
        {
            if (kind == "sale_return")
            {
                return new string[] { Key(SourceName(item), item.Id), "new:" + kind, "new:dispatch" };
            }
            // 采购退货单与到货单同表、共用卡片 26 的编号：两边生单互锁 "new:arrival" / "new:purchase_return"。
            // 所有采购退货生单都持 "new:purchase_return"，参照到货单与参照订单的退货因此串行；订单行、到货行另在事务里加行锁重读。
            if (kind == "purchase_return" || kind == "arrival")
            {
                return new string[] { Key(SourceName(item), item.Id), "new:arrival", "new:purchase_return" };
            }
            return new string[] { Key(SourceName(item), item.Id), "new:" + kind };
        }

        static string SourceName(WorkItem item)
        {
            if (item.Source != null && item.Source.Name != null)
            {
                return item.Source.Name;
            }
            return item.Type.GenerateFrom ?? "";
        }

        static string Key(string kind, int id)
        {
            return kind + ":" + id.ToString(CultureInfo.InvariantCulture);
        }
    }
}
