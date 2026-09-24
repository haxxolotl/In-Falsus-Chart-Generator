using System;
using System.Diagnostics;
using System.IO;
using System.Text;

// A normal CPython process preserves the native codec's callback ABI.
internal static class Launcher {
    private static string Quote(string value) {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value) {
            if (c == '\\') { slashes++; continue; }
            result.Append('\\', c == '"' ? slashes * 2 + 1 : slashes);
            result.Append(c); slashes = 0;
        }
        result.Append('\\', slashes * 2);
        return result.Append('"').ToString();
    }
    [STAThread]
    private static int Main(string[] args) {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        try {
#if CLI
            if (args.Length == 0) args = new string[] { "--help" };
#endif
            var arguments = new StringBuilder(Quote(Path.Combine(root, "scripts", "run.py")));
            foreach (string arg in args) arguments.Append(' ').Append(Quote(arg));
            var info = new ProcessStartInfo(Path.Combine(root, "runtime", "python.exe"), arguments.ToString());
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
#if CLI
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
#endif
            info.EnvironmentVariables.Remove("PYTHONHOME");
            info.EnvironmentVariables.Remove("PYTHONPATH");
            info.EnvironmentVariables["TCL_LIBRARY"] = Path.Combine(root, "runtime", "tcl");
            info.EnvironmentVariables["TK_LIBRARY"] = Path.Combine(root, "runtime", "tk");
            info.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
            using (var child = Process.Start(info)) {
#if CLI
                child.OutputDataReceived += (sender, e) => { if (e.Data != null) Console.Out.WriteLine(e.Data); };
                child.ErrorDataReceived += (sender, e) => { if (e.Data != null) Console.Error.WriteLine(e.Data); };
                child.BeginOutputReadLine();
                child.BeginErrorReadLine();
#endif
                child.WaitForExit(); return child.ExitCode;
            }
        } catch (Exception error) {
            string log = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "InFalsusStudio");
            Directory.CreateDirectory(log);
            File.WriteAllText(Path.Combine(log, "last-error.txt"), error.ToString());
#if CLI
            Console.Error.WriteLine(error.Message);
#endif
            return 1;
        }
    }
}
