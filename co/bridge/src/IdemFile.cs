using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;

namespace U8Co
{
    // 幂等记录的落盘原语。任何时刻磁盘上都不会出现「记录文件没了」的中间状态：
    // 首次写用 CreateNew + WriteThrough 直接建正式文件，不经过改名；
    // 之后的改写先 WriteThrough 写临时文件，再 MoveFileEx(REPLACE_EXISTING | WRITE_THROUGH) 原子替换，
    // 改名本身也在返回前落盘。File.Replace（ReplaceFile）失败时可能先删掉旧文件，所以不用它。
    internal static class IdemFile
    {
        const int MoveReplaceExisting = 0x1;
        const int MoveWriteThrough = 0x8;

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool MoveFileEx(string existing, string target, int flags);

        // 文件已存在时抛 IOException，由调用方按「已有记录」处理。
        public static void CreateNew(string file, byte[] data)
        {
            using (FileStream fs = new FileStream(file, FileMode.CreateNew, FileAccess.Write, FileShare.None, 4096, FileOptions.WriteThrough))
            {
                fs.Write(data, 0, data.Length);
                fs.Flush(true);
            }
        }

        public static void Replace(string file, byte[] data)
        {
            string tmp = file + ".tmp";
            using (FileStream fs = new FileStream(tmp, FileMode.Create, FileAccess.Write, FileShare.None, 4096, FileOptions.WriteThrough))
            {
                fs.Write(data, 0, data.Length);
                fs.Flush(true);
            }
            if (!MoveFileEx(tmp, file, MoveReplaceExisting | MoveWriteThrough))
            {
                int code = Marshal.GetLastWin32Error();
                throw new IOException("幂等记录替换失败", new Win32Exception(code));
            }
        }
    }
}
