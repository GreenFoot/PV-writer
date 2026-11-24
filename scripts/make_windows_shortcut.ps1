# Create "PV Writer" shortcuts (Start menu and desktop) for running PV Writer from source.
#
# Started with pythonw, the taskbar shows "pythonw" / "Python" for pinned and recent items.
# main.py gives the process the AppUserModelID "pvwriter.meeting.minutes.1"; a Start menu
# shortcut carrying the same ID tells Windows the name and icon to show for it.
#
# Usage (PowerShell, from the project folder):
#   powershell -ExecutionPolicy Bypass -File scripts\make_windows_shortcut.ps1

$ErrorActionPreference = "Stop"
$AppId = "pvwriter.meeting.minutes.1"  # keep in sync with _set_windows_app_id() in main.py
$Root = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $Pythonw)) {
    Write-Error "Environnement .venv introuvable : créez-le d'abord (voir README)."
}
$Icon = Join-Path $Root "assets\icons\pv_writer.ico"
$Main = Join-Path $Root "main.py"

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPropertyStore {
    void GetCount(out uint count);
    void GetAt(uint index, out PropertyKey key);
    void GetValue(ref PropertyKey key, out PropVariant value);
    void SetValue(ref PropertyKey key, ref PropVariant value);
    void Commit();
}

[StructLayout(LayoutKind.Sequential, Pack = 4)]
public struct PropertyKey {
    public Guid FormatId;
    public uint PropertyId;
}

[StructLayout(LayoutKind.Explicit)]
public struct PropVariant {
    [FieldOffset(0)] public ushort VarType;
    [FieldOffset(8)] public IntPtr Pointer;
}

public static class ShortcutAppId {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern void SHGetPropertyStoreFromParsingName(
        string path, IntPtr bindContext, int flags, ref Guid iid,
        [MarshalAs(UnmanagedType.Interface)] out IPropertyStore store);

    public static void Set(string shortcutPath, string appId) {
        Guid iid = typeof(IPropertyStore).GUID;
        IPropertyStore store;
        SHGetPropertyStoreFromParsingName(shortcutPath, IntPtr.Zero, 2 /* GPS_READWRITE */, ref iid, out store);
        PropertyKey key = new PropertyKey {
            FormatId = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), PropertyId = 5  // System.AppUserModel.ID
        };
        PropVariant value = new PropVariant { VarType = 31 /* VT_LPWSTR */, Pointer = Marshal.StringToCoTaskMemUni(appId) };
        try {
            store.SetValue(ref key, ref value);
            store.Commit();
        } finally {
            Marshal.FreeCoTaskMem(value.Pointer);
            Marshal.ReleaseComObject(store);
        }
    }
}
"@

$Shell = New-Object -ComObject WScript.Shell
$Targets = @(
    (Join-Path ([Environment]::GetFolderPath("Programs")) "PV Writer.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "PV Writer.lnk")
)
foreach ($Path in $Targets) {
    $Link = $Shell.CreateShortcut($Path)
    $Link.TargetPath = $Pythonw
    $Link.Arguments = "`"$Main`""
    $Link.WorkingDirectory = $Root
    $Link.IconLocation = "$Icon,0"
    $Link.Description = "PV Writer : procès-verbaux de réunion"
    $Link.Save()
    [ShortcutAppId]::Set($Path, $AppId)
    Write-Host "Créé : $Path"
}
Write-Host "Lancez PV Writer depuis le menu Démarrer ou le bureau ; épinglez-le à la barre des tâches depuis le menu Démarrer."
