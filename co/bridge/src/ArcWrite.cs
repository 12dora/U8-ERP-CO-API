using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace U8Co
{
    // 档案新增、修改、删除。Transact 自己提交（不在 CoTrans 里），所以先校验完再调用，之后在新连接上回读。
    internal static class ArcWrite
    {
        public static ApiResult Create(WorkContext ctx, ArcReq req)
        {
            ArcGuard.Permit(ctx, req);
            object conn = ctx.Conn;
            if (ArcRead.Exists(conn, req.Kind, req.Code))
            {
                throw new BridgeException(409, "state_mismatch", "档案编码已存在：" + req.Code);
            }
            ArcBag bag = new ArcBag();
            if (req.Template != null)
            {
                ArcTpl.Fill(conn, req, bag);
            }
            IList<string> tags = req.Fields.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                bag.Put(tags[i], req.Fields.Get(tags[i]));
            }
            ArcTpl.Defaults(ctx, req, bag);
            ArcGuard.Check(ctx, req, bag, null);
            return Run(ctx, req, "add", ArcXml.Envelope(req, "add", bag));
        }

        // diffedit（原因码是 edit，ArcKind.EditProc）：当前行所有可写的非空标签打底（U8 对必输项在修改时也检查），调用方字段覆盖；
        // ArcKind.Resend 只在当前值为空时补缺省（如人员证件类型 0）。
        public static ApiResult Update(WorkContext ctx, ArcReq req)
        {
            ArcGuard.Permit(ctx, req);
            Dictionary<string, string> row = ArcRead.Row(ctx.Conn, req.Kind, req.Code);
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "档案不存在");
            }
            ArcBag bag = new ArcBag();
            ArcTpl.Current(req, row, bag);
            Current(req, row, req.Kind.Resend, bag);
            IList<string> tags = req.Fields.Tags;
            for (int i = 0; i < tags.Count; i++)
            {
                bag.Put(tags[i], req.Fields.Get(tags[i]));
            }
            ArcGuard.Check(ctx, req, bag, row);
            return Run(ctx, req, req.Kind.EditProc, ArcXml.Envelope(req, req.Kind.EditProc, bag));
        }

        // 被单据引用的档案由 U8 自己拒绝删除（ArchIsUsed），原文 409 带回；货位、计量单位和计量单位组、结算方式等七类档案
        // 另由桥先查下级和引用（ArcGuard.Delete）。
        public static ApiResult Delete(WorkContext ctx, ArcReq req)
        {
            ArcGuard.Permit(ctx, req);
            Dictionary<string, string> row = ArcRead.Row(ctx.Conn, req.Kind, req.Code);
            if (row == null)
            {
                throw new BridgeException(404, "not_found", "档案不存在");
            }
            ArcGuard.Delete(ctx.Conn, req, row);
            ArcBag bag = new ArcBag();
            Current(req, row, req.Kind.DeleteTags, bag);
            return Run(ctx, req, "delete", ArcXml.Envelope(req, "delete", bag));
        }

        static void Current(ArcReq req, Dictionary<string, string> row, string[] names, ArcBag bag)
        {
            for (int i = 0; i < names.Length; i++)
            {
                string tag = req.Map.Canon(names[i]);
                if (tag == null || req.Fields.Has(tag))
                {
                    continue;
                }
                string value;
                if (!row.TryGetValue(req.Map.Column(tag), out value) || value.Trim().Length == 0)
                {
                    value = req.Kind.DefaultOf(tag);
                }
                bag.Put(tag, value);
            }
        }

        static ApiResult Run(WorkContext ctx, ArcReq req, string proc, string xml)
        {
            ArcDryRun.Stop(ctx, req.Kind.Name, req.Code, req.Op, "U8SrvTrans.IClsCommon.Transact");
            string raw;
            try
            {
                raw = ArcXml.Transact(ctx, xml);
            }
            catch (COMException ex)
            {
                raw = ex.Message;
            }
            string dsc;
            int outcome = ArcXml.Outcome(raw, out dsc);
            if (outcome == 0)
            {
                throw new BridgeException(409, "u8_rejected", Rejected(dsc));
            }
            if (outcome < 0 && proc == req.Kind.EditProc)
            {
                CoRows.Note(ctx.Item, "arc " + ArcXml.Short(raw));
                throw new BridgeException(504, "outcome_unknown", "U8 返回无法识别，档案 " + req.Code + " 的修改结果未知");
            }
            bool wanted = proc != "delete";
            if (Present(ctx, req) == wanted)
            {
                return Done(req, proc);
            }
            if (outcome > 0)
            {
                throw new BridgeException(504, "outcome_unknown", Unknown(req, proc));
            }
            throw new BridgeException(409, "u8_rejected", ArcXml.Short(raw));
        }

        // 「…不可为空」来自 U8 档案设置里的必输项，调用方需要在 fields 里给出。
        static string Rejected(string dsc)
        {
            if (dsc.IndexOf("不可为空", StringComparison.Ordinal) >= 0)
            {
                return dsc + "（U8 档案设置为必输）";
            }
            return dsc;
        }

        static bool Present(WorkContext ctx, ArcReq req)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                return ArcRead.Exists(conn, req.Kind, req.Code);
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "arc readback " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", Unknown(req, "readback"));
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        static string Unknown(ArcReq req, string proc)
        {
            if (proc == "delete")
            {
                return "U8 返回已删除档案 " + req.Code + "，但回读仍能查到，结果未知";
            }
            if (proc == "readback")
            {
                return "U8 已处理档案 " + req.Code + "，但回读失败，结果未知";
            }
            return "U8 返回已保存档案 " + req.Code + "，但回读查不到，结果未知";
        }

        static ApiResult Done(ArcReq req, string proc)
        {
            Dictionary<string, object> body = ArcRead.Head(req);
            if (proc == "delete")
            {
                body["deleted"] = true;
            }
            return ApiResult.Ok(body);
        }
    }
}
