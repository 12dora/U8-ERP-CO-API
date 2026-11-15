using System;

namespace U8Co
{
    internal static class WorkflowGate
    {
        public static void Ensure(object conn, bool saleOrder, HeadState head)
        {
            string table = saleOrder ? "SO_SOMain" : "DispatchList";
            if (!AdoXml.HasBizObject(conn, table))
            {
                throw new BridgeException(409, "workflow_unknown", "AuditBizObjects 没有该单据表");
            }
            if (head.Workflow || AdoXml.WorkflowReleased(conn, table))
            {
                throw new BridgeException(409, "workflow_enabled", "单据已启用审批流，本期不支持");
            }
        }
    }
}
