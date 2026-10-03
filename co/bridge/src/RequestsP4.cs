using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 这一组路由：总账凭证、档案、列表（不带单据 id），以及新单据类型在登录前的校验。
    // 这里只认字段名；字段值的校验在各领域类的 Check / CheckCreate / CheckGenerate 里，登录前抛 400。
    internal static partial class Requests
    {
        internal const string GlRoot = "/u8co/v1/gl/vouchers/";
        internal const string ArcRoot = "/u8co/v1/archives/";
        internal const string ListPath = "/u8co/v1/vouchers/list";
        internal const string StockPath = "/u8co/v1/stock/current";

        static readonly string[][] P4Specs = new string[][]
        {
            new string[] { GlRoot + "load", "period", "sign", "no" },
            new string[]
            {
                GlRoot + "list", "period_from", "period_to", "sign", "date_from", "date_to", "maker", "state",
                "after", "limit"
            },
            // 凭证摘要（GlDigest，事件源）。
            new string[] { GlRoot + "digest", "fiscal_year", "periods", "closed_periods", "after", "limit", "keys_only" },
            new string[] { GlRoot + "create", "head", "lines" },
            new string[] { GlRoot + "update", "period", "sign", "no", "head", "lines" },
            new string[] { GlRoot + "void", "period", "sign", "no" },
            new string[] { GlRoot + "unvoid", "period", "sign", "no" },
            new string[] { GlRoot + "verify", "period", "sign", "no" },
            new string[] { GlRoot + "unverify", "period", "sign", "no" },
            new string[] { GlRoot + "sign", "period", "sign", "no" },
            new string[] { GlRoot + "unsign", "period", "sign", "no" },
            new string[] { GlRoot + "delete", "period", "sign", "no" },
            // 记账（GlPost）：vouchers 是 [{sign, no}]，fiscal_year 省略取登录日期的年份。
            new string[] { GlRoot + "post", "period", "vouchers", "fiscal_year" },
            // 取消记账（GlUnpost，测试账套）：都可选，period / vouchers 用来核对最近一次记账的范围。
            new string[] { GlUnpostReq.Path, "fiscal_year", "period", "vouchers" },
            // 红字冲销（GlReverse）：原凭证 period、sign、no，fiscal_year 省略取登录年度，voucher_date 是红字凭证日期。
            new string[] { GlReverseReq.Path, "period", "sign", "no", "fiscal_year", "voucher_date" },
            // 期间损益结转、自定义转账（GlTransferReq，处理在 GlTransfer，测试账套）：dry_run、幂等键在字段校验前已取走。
            GlTransferReq.PnlSpec,
            GlTransferReq.CustomSpec,
            // 凭证附件列表（GlAttach，只读）。
            new string[] { GlAttach.Path, "period", "sign", "no" },
            new string[] { ArcRoot + "get", "archive", "code" },
            // project_class：只读档案 project 的列表条件（ArcProject）；currency、fiscal_year：只读档案 exchange_rate
            // 的列表条件（ArcExch，ArcRoutes 对其他档案 400）；type_code、dept_code、include_disposed：只读档案 fa_card（ArcFa）。
            // keys_only：全部档案，每行只留 code、ufts（ArcListExtra）。
            new string[]
            {
                ArcRoot + "list", "archive", "code_prefix", "name_like", "changed_since", "after", "limit", "project_class",
                "currency", "fiscal_year", "type_code", "dept_code", "include_disposed", "keys_only"
            },
            new string[] { ArcRoot + "create", "archive", "code", "fields", "template" },
            new string[] { ArcRoot + "update", "archive", "code", "fields" },
            new string[] { ArcRoot + "delete", "archive", "code" },
            new string[] { ListPath, "type", "filter", "keys_only", "changed_since", "after", "limit" },
            // 单据搜索（VoucherSearchArgs）、单据批量读取（LoadManyReq）、档案批量读取（ArcGetMany）。
            new string[]
            {
                VoucherSearch.Path, "type", "code_like", "partner", "dept", "person", "warehouse", "maker", "inventory",
                "date_from", "date_to", "verified", "closed", "after", "limit", "defines"
            },
            new string[] { LoadMany.Path, "type", "ids" },
            new string[] { ArcGetMany.Path, "archive", "codes" },
            // 字段说明（MetaFieldsReq）：type / archive / gl 三选一。
            new string[] { MetaFieldsReq.Path, "type", "archive", "gl", "op", "source" },
            new string[] { StockPath, "wh", "inv", "batch", "nonzero", "changed_since", "after", "limit" },
            // 期初记账 / 取消记账（OpeningPostReq）：dry_run、幂等键在字段校验前已取走。
            new string[] { OpeningPostReq.Path, "module", "action" },
            // 应收 / 应付期初单据（OpeningsArapReq）：dry_run、幂等键同上。
            OpeningsArapReq.Spec,
            // 月末结账 / 取消结账（PeriodCloseReq）：dry_run、幂等键同上。
            new string[] { PeriodCloseReq.Path, "module", "fiscal_year", "period", "action", "through" },
            // 存货核算记账、期末处理（IaReq）：dry_run、幂等键同上。
            IaReq.PostSpec,
            IaReq.PeriodEndSpec,
            // 并账（ArapMergeReq，处理在 ArapMerge）：dry_run、幂等键同上。
            ArapMergeReq.Spec,
            // 处理制单（ArapProcVoucherReq，处理在 ArapProcVoucher）：dry_run、幂等键同上。
            ArapProcVoucherReq.Spec,
            // 取消应收冲应付 / 应付冲应收 / 并账（ArapProcCancelReq）：dry_run、幂等键同上。
            ArapProcCancelReq.Spec,
            // 应收冲应付 / 应付冲应收（ArapTransferReq，处理在 ArapTransfer）：dry_run、幂等键同上。
            ArapTransferReq.Spec,
            // 红票对冲（ArapRedReq，处理在 ArapRed）：dry_run、幂等键同上。
            ArapRedReq.Spec,
            // 票据处理（NotesProcReq，处理在 NotesProc）：dry_run、幂等键同上。
            NotesProcReq.Spec,
            // 票据登记、删除（NotesRegReq，处理在 NotesReg、NotesRegDel）：dry_run、幂等键同上。
            NotesRegReq.CreateSpec,
            NotesRegReq.DeleteSpec,
            // 汇兑损益、取消汇兑损益（ArapExGainReq，处理在 ArapExGain / ArapExGainCancel）：dry_run、幂等键同上。
            ArapExGainReq.Spec,
            ArapExGainReq.CancelSpec,
            // 坏账处理（ArapBadReq，处理在 ArapBad，按 action 分派）：dry_run、幂等键同上。
            ArapBadReq.Spec,
            // 单张票据读取（NotesReadReq，处理在 NotesRead）：只读。
            NotesReadReq.Spec,
            // 应收 / 应付处理记录与期间摘要（ArapProcListReq，处理在 ArapProcList）：只读。
            ArapProcListReq.Spec,
            // 权限快照（本人）、权限评估（subject，限 permEvaluateOperators）：只读（PermSnapshot、PermEvaluate）。
            PermSnapshot.Spec,
            PermEvaluate.Spec
        };

        // 不带 type 的路由：总账、档案、现存量。vouchers/list 带 type。
        static bool Typeless(string path)
        {
            // 只读报表不带 type。
            if (IsReport(path))
            {
                return true;
            }
            // 应收 / 应付核销（RequestsWriteoff）同样不带 type。
            // 取消制单（RequestsArapVoucher）不带 type、id；制单带 type、id，照常解析。
            // 幂等结果查询（IdemGet）不带 type、id。
            // 期初记账、月末结账，存货核算、应收应付期初单据（IsLedgerRoute，RequestsIa.cs）同样不带 type、id。
            return IsGl(path) || IsArc(path) || path == StockPath || IsWriteoff(path) || path == ArapVoucherDropPath
                || MetaReadPath(path) || IsLedgerOrProcList(path);
        }

        // 列表与搜索里只是列表类型、不是单据类型的（票据 ar_note / ap_note），不按单据类型校验，交给 ListArgs。
        static bool ListOnlyType(string path, Dictionary<string, object> body)
        {
            object type;
            if ((path != ListPath && path != VoucherSearch.Path) || body == null || !body.TryGetValue("type", out type))
            {
                return false;
            }
            string name = type as string;
            return name != null && Kinds.Find(name) == null && ListKinds.Find(name) != null;
        }

        static bool NoIdP4(string path)
        {
            return Typeless(path) || path == ListPath || path == VoucherSearch.Path || path == LoadMany.Path;
        }

        static bool IsGl(string path)
        {
            return path != null && path.StartsWith(GlRoot, StringComparison.Ordinal);
        }

        static bool IsArc(string path)
        {
            return path != null && path.StartsWith(ArcRoot, StringComparison.Ordinal);
        }

        // 领域类 Handle / Check 用的 op：总账 load…delete，档案 get…delete，列表 vouchers/list、stock/current。
        internal static string OpOf(string path)
        {
            if (IsGl(path))
            {
                return path.Substring(GlRoot.Length);
            }
            if (IsArc(path))
            {
                return path.Substring(ArcRoot.Length);
            }
            if (path == ListPath || path == StockPath)
            {
                return path.Substring("/u8co/v1/".Length);
            }
            return "";
        }

        // 审计 action：gl_<op>、archive_<op>、list、stock_current；其他路由返回 null，照旧处理。
        static string ActionP4(string path)
        {
            // 只读报表：report_<name>。
            if (IsReport(path))
            {
                return "report_" + ReportName(path);
            }
            if (IsGl(path))
            {
                return "gl_" + OpOf(path);
            }
            if (IsArc(path))
            {
                return "archive_" + OpOf(path);
            }
            if (path == ListPath)
            {
                return "list";
            }
            if (path == StockPath)
            {
                return "stock_current";
            }
            // 应收 / 应付制单、取消制单：arap_voucher / arap_voucher_delete。
            if (IsArapVoucher(path))
            {
                return ArapVoucherAction(path);
            }
            // 幂等结果查询：idempotency_get。
            if (path == IdemGet.Path)
            {
                return IdemGet.Action;
            }
            // 字段说明：meta_fields；权限快照、评估：perm_snapshot / perm_evaluate（MetaReadAction，PermEvaluate.cs）。
            string meta = MetaReadAction(path);
            if (meta != null)
            {
                return meta;
            }
            // 单据搜索、单据批量读取：search / load_many（BatchAction，LoadManyRequests.cs）。
            // 期初记账：opening_post（OpeningAction，RequestsOpening.cs）；月末结账：period_close（PeriodAction，RequestsPeriod.cs）；
            // 存货核算：ia_post / ia_period_end（IaAction，RequestsIa.cs）。
            return IsWriteoff(path) ? WriteoffAction(path) : OpeningAction(path);
        }

        static void ApplyP4(Dictionary<string, object> body, WorkItem item, string path)
        {
            // 口令密文已解密，不带进任务。
            body.Remove("password_enc");
            item.Body = body;
            string op = OpOf(path);
            // 只读报表。
            if (IsReport(path))
            {
                ApplyReport(body, item, path);
                return;
            }
            if (IsGl(path))
            {
                GlRoutes.Check(op, body);
                // 取消记账是第二级写入：登录前查测试账套名单（GlUnpost 入队后再查一次）。
                if (path == GlUnpostReq.Path)
                {
                    TestAccountGate.Require(item, GlUnpostReq.TestOnly);
                }
                item.SubId = "GL";
                NoteGl(item, body);
                return;
            }
            if (IsArc(path))
            {
                ArcRoutes.Check(op, body);
                // 币种、凭证类别的新增（EAI）按 ArcGlKinds.WriteSub 登录；修改、删除只跑 SQL，照旧 AS。
                item.SubId = ArcGlKinds.SubOf(op, body) ?? "AS";
                NoteArc(item, body);
                return;
            }
            if (path == ListPath || path == StockPath)
            {
                ListRoutes.Check(op, body);
                item.SubId = ListSub(path, body);
                return;
            }
            if (IsWriteoff(path))
            {
                ApplyWriteoff(body, item);
                return;
            }
            // 应收 / 应付制单、取消制单（RequestsArapVoucher）：登录子系统是 flag。
            if (IsArapVoucher(path))
            {
                ApplyArapVoucher(body, item);
                return;
            }
            ApplyRest(body, item, path);
        }

        static void ApplyRest(Dictionary<string, object> body, WorkItem item, string path)
        {
            // 应收 / 应付处理记录：登录前校验，登录子系统是 flag（ApplyProcList，ArapProcListReq.cs）。
            if (ApplyProcList(body, item, path))
            {
                return;
            }
            // 处理制单：登录前校验，汇兑损益、坏账、应付票据查测试账套名单，登录子系统是 flag（ApplyProcVoucher，RequestsProcVoucher.cs）。
            if (ApplyProcVoucher(body, item, path))
            {
                return;
            }
            // 期初记账 / 取消记账、期初单据：登录前校验、测试账套名单，定登录子系统（ApplyOpening，RequestsOpening.cs）。
            if (ApplyOpening(body, item, path))
            {
                return;
            }
            // 月末结账 / 取消结账：登录前校验、测试账套名单，定登录子系统（ApplyPeriod，RequestsPeriod.cs）。
            if (ApplyPeriod(body, item, path))
            {
                return;
            }
            // 存货核算记账、期末处理：登录前校验、测试账套名单，登录子系统 IA（ApplyIa，RequestsIa.cs）。
            if (ApplyIa(body, item, path))
            {
                return;
            }
            // 取消应收冲应付 / 应付冲应收 / 并账：登录前校验，登录子系统是 flag（ApplyArapProc，ArapProcCancelReq.cs）。
            if (ApplyArapProc(body, item, path))
            {
                return;
            }
            // 汇兑损益、取消汇兑损益：登录前校验、测试账套名单，登录子系统是 flag（ApplyExGain，RequestsExGain.cs）。
            if (ApplyExGain(body, item, path))
            {
                return;
            }
            // 坏账处理：登录前校验、测试账套名单，登录子系统 AR（ApplyBadDebt，ArapBadReq.cs）。
            if (ApplyBadDebt(body, item, path))
            {
                return;
            }
            ApplyReads(body, item, path);
        }

        // ApplyRest 的后段（控制圈复杂度拆出）：只读的元数据路由、批量读取，其余按类型校验。
        static void ApplyReads(Dictionary<string, object> body, WorkItem item, string path)
        {
            // 期间损益结转、自定义转账：登录前校验、测试账套名单，登录子系统 GL（ApplyGlTransfer，RequestsGlTransfer.cs）。
            if (ApplyGlTransfer(body, item, path))
            {
                return;
            }
            // 权限快照、权限评估：登录前校验 subject、调用操作员名单，登录子系统 AS（ApplyPerm，PermEvaluate.cs）。
            if (ApplyPerm(body, item, path))
            {
                return;
            }
            // 幂等结果查询、字段说明：登录前校验，定登录子系统（ApplyMetaRead，MetaFieldsRequests.cs）。
            if (ApplyMetaRead(body, item, path))
            {
                return;
            }
            // 单据搜索、单据批量读取在登录前校验，其余按类型校验（ApplyBatchOrKind，LoadManyRequests.cs）。
            ApplyBatchOrKind(body, item, path);
        }

        // 列表按单据类型的登录子系统（与读取一致），现存量用库存 ST。
        static string ListSub(string path, Dictionary<string, object> body)
        {
            if (path == StockPath)
            {
                return "ST";
            }
            string type = Requests.Field(body, "type") as string;
            VoucherKind kind = Kinds.Find(type);
            if (kind == null || string.IsNullOrEmpty(kind.SubId))
            {
                // 票据列表（ar_note / ap_note）按应收 / 应付登录，其余缺省 SA。
                return NotesReadReq.FlagOf(type) ?? "SA";
            }
            return kind.SubId;
        }

        // 收付款单、应收应付单新增和修改，各类生单：登录前先校验。生产订单审核改用 MO 登录。
        static void CheckKind(WorkItem item, string path)
        {
            VoucherKind kind = item.Type;
            if (kind == null)
            {
                return;
            }
            // 质量单据：生单、删除（报检单删除前的弃审）用 QM 登录，生单在登录前校验（QmReq）。
            if (QmReq.Check(item, path))
            {
                return;
            }
            if (path == "/u8co/v1/vouchers/create" && kind.Family == "ar")
            {
                ArapReq.CheckCreate(kind, item.Head, item.Lines);
            }
            else if (path == "/u8co/v1/vouchers/update" && kind.Family == "ar")
            {
                ArapEditReq.Parse(kind, item.Head, item.Lines);
            }
            else if (path == "/u8co/v1/vouchers/generate")
            {
                CheckGenerate(item, kind);
            }
            else if (path == "/u8co/v1/vouchers/verify" && !Empty(kind.VerifySub))
            {
                item.SubId = kind.VerifySub;
            }
            else
            {
                CheckMo(item, kind, path);
            }
        }

        // 生产订单新增 / 删除走 U8API，与审核一样用 MO 登录；新增的表头表体在登录前校验（MoCreateReq）。
        static void CheckMo(WorkItem item, VoucherKind kind, string path)
        {
            // K12 退货申请单：新增、修改的表头表体在登录前校验（ReturnsApplyReq）。
            if (ReturnsApplyReq.Check(item, path))
            {
                return;
            }
            // 物料清单（BomRoutes）：同样走 U8API，登录子系统 BO。
            if (kind.Name == BomRoutes.KindName)
            {
                BomRoutes.Check(item, path);
                return;
            }
            if (kind.Name != "production_order")
            {
                return;
            }
            if (path == "/u8co/v1/vouchers/create")
            {
                MoCreateReq.Parse(item.Head, item.Lines);
                item.SubId = kind.VerifySub;
            }
            else if (path == "/u8co/v1/vouchers/delete")
            {
                item.SubId = kind.VerifySub;
            }
            else if (path == "/u8co/v1/vouchers/update")
            {
                // 生产订单修改（MoUpdate）：登录前校验表头表体。
                MoUpdateReq.Parse(item.Head, item.Lines);
                item.SubId = kind.VerifySub;
            }
        }

        // 材料出库、产成品入库；采购发票参照采购入库；到货单参照采购订单；采购入库参照来料检验单、到货单。其余生单在登录后校验。
        static void CheckGenerate(WorkItem item, VoucherKind kind)
        {
            CheckSaleReturn(item, kind);
            string source = item.Source == null ? "" : item.Source.Name ?? "";
            if (kind.Name == "material_out" || kind.Name == "product_in")
            {
                MfgReq.CheckGenerate(kind, item.Head, item.Lines, source);
            }
            else if (kind.Name == "purchase_invoice")
            {
                PuInvReq.CheckGenerate(item.Head, item.Lines);
            }
            else if (kind.Name == "arrival")
            {
                PuArr.CheckGenerate(item.Head, item.Lines);
            }
            // 采购退货单（PuRet）：同到货单的表头表体规则。
            else if (kind.Name == "purchase_return")
            {
                PuRet.CheckGenerate(item.Head, item.Lines);
            }
            // 采购入库参照来料检验单、参照到货单（StockGen.CheckGenerateIn）。
            else if (kind.Name == "purchase_in")
            {
                StockGen.CheckGenerateIn(item);
            }
        }

        // 退货单参照蓝字发货单：表头表体字段、日期、行数在登录前校验。
        // 调拨单参照调拨申请单（StockGenTr）：表头表体字段、行数、数量也在登录前校验。
        static void CheckSaleReturn(WorkItem item, VoucherKind kind)
        {
            if (SaleReturn.Is(kind))
            {
                SaleGen.CheckReturn(item.Head, item.Lines);
            }
            else if (kind.Name == "transfer")
            {
                StockGen.CheckTrGenerate(item.Head, item.Lines);
            }
        }

        // 审计 detail 带上凭证号或档案编码，便于按键查审计。
        static void NoteGl(WorkItem item, Dictionary<string, object> body)
        {
            if (!body.ContainsKey("no"))
            {
                return;
            }
            CoRows.Note(item, "凭证 " + KeyPart(Field(body, "period")) + "-" + KeyPart(Field(body, "sign"))
                + "-" + KeyPart(Field(body, "no")));
        }

        static void NoteArc(WorkItem item, Dictionary<string, object> body)
        {
            if (!body.ContainsKey("code"))
            {
                return;
            }
            CoRows.Note(item, "档案 " + KeyPart(Field(body, "archive")) + " " + KeyPart(Field(body, "code")));
        }

        internal static object Field(Dictionary<string, object> map, string key)
        {
            object value;
            if (map == null || !map.TryGetValue(key, out value))
            {
                return null;
            }
            return value;
        }

        // 锁键和审计里的一段：整数按不变区域格式，字符串去两端空格；其他类型为空串。
        internal static string KeyPart(object value)
        {
            if (value is int)
            {
                return ((int)value).ToString(CultureInfo.InvariantCulture);
            }
            if (value is long)
            {
                return ((long)value).ToString(CultureInfo.InvariantCulture);
            }
            string text = value as string;
            return text == null ? "" : text.Trim();
        }
    }
}
