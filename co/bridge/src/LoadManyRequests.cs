using System.Collections.Generic;

namespace U8Co
{
    // 单据搜索（vouchers/search）、单据批量读取（vouchers/load_many）在 Requests 里的两处登记：审计 action 和登录前校验。
    // 字段表在 RequestsP4 的 P4Specs；档案批量读取（archives/get_many）走档案那一支（ArcRoutes.Check、archive_get_many）。
    internal static partial class Requests
    {
        // 审计 action：search / load_many；其他路由返回 null。
        static string BatchAction(string path)
        {
            if (path == VoucherSearch.Path)
            {
                return VoucherSearch.Action;
            }
            // 单张票据读取：note_get。
            if (NotesReadReq.IsPath(path))
            {
                return NotesReadReq.Action;
            }
            // 取消应收冲应付 / 应付冲应收 / 并账：arap_process_cancel（ArapProcAction，ArapProcCancelReq.cs）。
            // 应收 / 应付处理记录：arap_process_list（ProcListAction，ArapProcListReq.cs）。
            return path == LoadMany.Path ? LoadMany.Action : ProcListAction(path);
        }

        // 单据搜索：请求校验同处理函数（VoucherSearchArgs），登录子系统同 vouchers/list（ListSub）。
        // 单据批量读取：ids 校验（含 COM 类型 5 张上限），登录子系统同 vouchers/load（类型的 SubId），审计 detail 记 id 列表。
        // 其余路由照旧按单据类型校验（CheckKind）。
        static void ApplyBatchOrKind(Dictionary<string, object> body, WorkItem item, string path)
        {
            if (path == VoucherSearch.Path)
            {
                VoucherSearchArgs.Parse(body);
                item.SubId = ListSub(path, body);
                return;
            }
            if (path == LoadMany.Path)
            {
                LoadManyReq.Note(item, LoadManyReq.Parse(item.Type, body));
                return;
            }
            // 单张票据读取：登录前校验，登录子系统是票据的应收 / 应付（NotesReadReq）。
            if (NotesReadReq.IsPath(path))
            {
                item.SubId = NotesReadReq.Parse(body).Flag;
                return;
            }
            CheckKind(item, path);
        }
    }
}
