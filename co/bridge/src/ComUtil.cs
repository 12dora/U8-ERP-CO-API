using System;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;

namespace U8Co
{
    // U8 与 ADO/MSXML 都走 IDispatch。不能引用 Interop 程序集：那些 tlbimp 早于现行 DLL。
    internal static class ComUtil
    {
        const BindingFlags CallFlag = BindingFlags.InvokeMethod;
        const BindingFlags GetFlag = BindingFlags.GetProperty;
        const BindingFlags SetFlag = BindingFlags.SetProperty;

        public static object Create(string progId)
        {
            Type kind = Type.GetTypeFromProgID(progId, false);
            if (kind == null)
            {
                return null;
            }
            try
            {
                return Activator.CreateInstance(kind);
            }
            catch (Exception ex)
            {
                if (Unavailable(ex))
                {
                    throw new BridgeException(503, "com_unavailable", "组件无法创建 " + progId);
                }
                throw;
            }
        }

        static bool Unavailable(Exception ex)
        {
            Exception cur = ex;
            while (cur != null)
            {
                if (cur is COMException || cur is FileNotFoundException || cur is BadImageFormatException)
                {
                    return true;
                }
                cur = cur.InnerException;
            }
            return false;
        }

        public static object Call(object target, string name, object[] args)
        {
            try
            {
                return target.GetType().InvokeMember(name, CallFlag, null, target, args);
            }
            catch (TargetInvocationException ex)
            {
                if (ex.InnerException != null)
                {
                    throw ex.InnerException;
                }
                throw;
            }
        }

        // args 里 refIndexes 指出的槽位按引用传递，调用后写回同一数组。
        public static object CallRef(object target, string name, object[] args, int[] refIndexes)
        {
            if (refIndexes == null || refIndexes.Length == 0)
            {
                return Call(target, name, args);
            }
            if (args == null)
            {
                throw new BridgeException(500, "internal", "引用参数缺少实参");
            }
            ParameterModifier mod = new ParameterModifier(args.Length);
            for (int i = 0; i < refIndexes.Length; i++)
            {
                int index = refIndexes[i];
                if (index < 0 || index >= args.Length)
                {
                    throw new BridgeException(500, "internal", "引用参数序号无效");
                }
                mod[index] = true;
            }
            try
            {
                return target.GetType().InvokeMember(
                    name, CallFlag, null, target, args, new ParameterModifier[] { mod }, null, null);
            }
            catch (TargetInvocationException ex)
            {
                if (ex.InnerException != null)
                {
                    throw ex.InnerException;
                }
                throw;
            }
        }

        public static object Get(object target, string name)
        {
            try
            {
                return target.GetType().InvokeMember(name, GetFlag, null, target, null);
            }
            catch (TargetInvocationException ex)
            {
                if (ex.InnerException != null)
                {
                    throw ex.InnerException;
                }
                throw;
            }
        }

        // 带参数的属性读取（索引器，如 U8 扩展实体的 Item(i)）：IDispatch propget，托管对象按索引器取。
        public static object GetAt(object target, string name, object[] args)
        {
            try
            {
                return target.GetType().InvokeMember(name, GetFlag, null, target, args);
            }
            catch (TargetInvocationException ex)
            {
                if (ex.InnerException != null)
                {
                    throw ex.InnerException;
                }
                throw;
            }
        }

        public static void Set(object target, string name, object value)
        {
            try
            {
                target.GetType().InvokeMember(name, SetFlag, null, target, new object[] { value });
            }
            catch (TargetInvocationException ex)
            {
                if (ex.InnerException != null)
                {
                    throw ex.InnerException;
                }
                throw;
            }
        }

        // 我们 Create 出来的对象在 finally 里一次放光。子对象（Parameters、Fields）
        // 已经交给父对象，不能 FinalRelease，否则 ADO 还在用的参数会被拆掉。
        public static void Final(object target)
        {
            if (target == null)
            {
                return;
            }
            try
            {
                if (Marshal.IsComObject(target))
                {
                    Marshal.FinalReleaseComObject(target);
                }
            }
            catch (Exception)
            {
                // 某个对象释放失败不能挡住 finally 里其余对象。
            }
        }

        public static void ReleaseOne(object target)
        {
            if (target == null)
            {
                return;
            }
            try
            {
                if (Marshal.IsComObject(target))
                {
                    Marshal.ReleaseComObject(target);
                }
            }
            catch (Exception)
            {
            }
        }
    }

    internal static class Values
    {
        public static string Text(object value)
        {
            if (value == null || value is DBNull)
            {
                return "";
            }
            return Convert.ToString(value);
        }

        public static bool Flag(object value)
        {
            if (value == null || value is DBNull)
            {
                return false;
            }
            if (value is bool)
            {
                return (bool)value;
            }
            string text = value as string;
            if (text != null)
            {
                return text == "1" || text.Equals("true", StringComparison.OrdinalIgnoreCase);
            }
            try
            {
                return Convert.ToInt64(value) != 0;
            }
            catch (Exception)
            {
                return false;
            }
        }

        // 不用本机区域格式，避免审核时间在不同系统上变成不同的字符串。
        public static string Time(object value)
        {
            if (value == null || value is DBNull)
            {
                return "";
            }
            if (value is DateTime)
            {
                return ((DateTime)value).ToString("yyyy-MM-ddTHH:mm:ss", CultureInfo.InvariantCulture);
            }
            return Convert.ToString(value);
        }
    }
}
