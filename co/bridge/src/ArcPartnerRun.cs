using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace U8Co
{
    internal delegate string PartnerCall();

    // 回读核对：在新连接上判断这次写入是否已按预期落库。
    internal delegate bool PartnerCheck(object conn);

    // 子档案的 EAI 写入：组件自己提交（不包 CoTrans），调用前查完能查的；调用后在新连接上回读核对。
    // U8 明确拒绝（succeed 非 0）是 409 u8_rejected；U8 说成功但回读不符、回读失败，是 504 outcome_unknown；
    // 返回无法识别时按回读判断，修改回读不符同样 504（分不清是否已改）。
    internal static class ArcPartnerRun
    {
        public static ApiResult Eai(WorkContext ctx, ArcReq req, string proc, PartnerCall call, PartnerCheck check)
        {
            return Eai(ctx, req, proc, ArcPartnerContact.CustomerProgId + ".Transact", call, check);
        }

        // what：预演停下处的组件名（汇率、供应商联系人新增走 EAI 分发器 EaiDistribute.What）。
        public static ApiResult Eai(WorkContext ctx, ArcReq req, string proc, string what, PartnerCall call, PartnerCheck check)
        {
            ArcDryRun.Stop(ctx, req.Kind.Name, req.Code, req.Op, what);
            string raw;
            try
            {
                raw = call();
            }
            catch (COMException ex)
            {
                Dispatch(ex);
                raw = ex.Message;
            }
            string dsc;
            int outcome = ArcXml.Outcome(raw, out dsc);
            if (outcome == 0)
            {
                throw new BridgeException(409, "u8_rejected", dsc);
            }
            if (Fresh(ctx, req, check))
            {
                return Done(req);
            }
            CoRows.Note(ctx.Item, "partner " + proc + " " + ArcXml.Short(raw));
            if (outcome > 0 || req.Op == "update")
            {
                throw new BridgeException(504, "outcome_unknown", Unknown(req));
            }
            throw new BridgeException(409, "u8_rejected", ArcXml.Short(raw));
        }

        // 组件没有这个成员、参数个数或类型不对：调用方式与 U8 不符，是环境问题，503 com_unavailable（不当 U8 拒绝）。
        static void Dispatch(COMException ex)
        {
            int hr = ex.ErrorCode;
            if (hr == unchecked((int)0x80020006) || hr == unchecked((int)0x8002000E) || hr == unchecked((int)0x80020005))
            {
                throw new BridgeException(503, "com_unavailable", "U8 组件的调用方式不符：" + ex.Message);
            }
        }

        // 已提交（EAI 自己提交或受控 SQL 已 Commit）后的回读。读失败 504。
        public static bool Fresh(WorkContext ctx, ArcReq req, PartnerCheck check)
        {
            object conn = null;
            try
            {
                conn = ctx.OpenFresh();
                return check(conn);
            }
            catch (BridgeException)
            {
                // 核对函数自己判定的结果（例如联系人新增找到了不唯一的新行），原样带回。
                throw;
            }
            catch (Exception ex)
            {
                CoRows.Note(ctx.Item, "partner readback " + ex.Message);
                throw new BridgeException(504, "outcome_unknown", "U8 已处理档案 " + req.Code + "，但回读失败，结果未知，请先 get 核对，不要直接重发");
            }
            finally
            {
                AdoXml.Close(conn);
            }
        }

        public static string Unknown(ArcReq req)
        {
            if (req.Op == "delete")
            {
                return "U8 返回已删除档案 " + req.Code + "，但回读仍能查到，结果未知";
            }
            return "U8 返回已保存档案 " + req.Code + "，但回读与写入不一致，结果未知，请先 get 核对，不要直接重发";
        }

        public static ApiResult Done(ArcReq req)
        {
            Dictionary<string, object> body = ArcRead.Head(req);
            if (req.Op == "delete")
            {
                body["deleted"] = true;
            }
            return ApiResult.Ok(body);
        }

        // 编码的两段：{ 客户或供应商编码, 账号或联系人编码 }。
        public static string[] Parts(ArcReq req)
        {
            object[] parts = ArcPair.KeyArgs(req.Kind, req.Code);
            return new string[] { (string)parts[0], (string)parts[1] };
        }

        // 上级客户、供应商必须存在，返回编码行。
        public static Dictionary<string, object> Partner(object conn, PartnerSide side, string code)
        {
            Dictionary<string, object> row = Rows.One(conn, "SELECT " + side.CodeCol + " FROM " + side.Table + " WHERE " + side.CodeCol + "=?",
                new object[] { code });
            if (row == null)
            {
                throw ArcReq.Bad(side.Label + " " + code + " 不存在", "code");
            }
            return row;
        }

        public static void Text(ArcReq req, string tag, int max)
        {
            string value = req.Fields.Get(tag);
            if (value != null && value.Length > max)
            {
                throw ArcReq.Bad("字段 " + tag + " 最长 " + max.ToString(System.Globalization.CultureInfo.InvariantCulture) + " 个字符", FieldPath.Join("fields", tag));
            }
        }
    }
}
