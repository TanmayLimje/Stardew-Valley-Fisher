"""Find windows using ctypes directly."""
import ctypes
import ctypes.wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

EnumWindows = user32.EnumWindows
GetWindowTextW = user32.GetWindowTextW
GetWindowTextLengthW = user32.GetWindowTextLengthW
IsWindowVisible = user32.IsWindowVisible

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

windows = []

def enum_callback(hwnd, lparam):
    length = GetWindowTextLengthW(hwnd)
    if length > 0:
        buf = ctypes.create_unicode_buffer(length + 1)
        GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        visible = IsWindowVisible(hwnd)
        windows.append((hwnd, title, visible))
    return True

EnumWindows(WNDENUMPROC(enum_callback), 0)

print(f"Total titled windows: {len(windows)}")
print()

stardew = [(h, t, v) for h, t, v in windows if "stardew" in t.lower()]
if stardew:
    print("STARDEW VALLEY windows found:")
    for h, t, v in stardew:
        print(f"  hwnd={h} visible={v} title=\"{t}\"")
else:
    print("No 'stardew' in window titles.")
    print()
    print("All visible windows:")
    for h, t, v in sorted(windows, key=lambda x: x[1].lower()):
        if v:
            print(f"  hwnd={h:8d} \"{t}\"")
