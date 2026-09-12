"""Direct inline GDI capture test - try all three monitors."""
import sys
import numpy as np

try:
    import win32gui
    import win32ui
    import win32con
    import cv2
except ImportError as e:
    print(f"Missing dependency: {e}")
    sys.exit(1)

monitors = [
    ("Screen 1 (Laptop)",  -1920, 0, 1920, 1080),
    ("Screen 2 (Primary)",     0, 0, 1920, 1080),
    ("Screen 3 (Game)",     1920, 0, 1920, 1080),
]

for name, left, top, width, height in monitors:
    print(f"\n--- Testing {name} [{left},{top} -> {left+width},{top+height}] ---")
    try:
        hdesktop = win32gui.GetDesktopWindow()
        hdc = win32gui.GetWindowDC(hdesktop)
        mdc = win32ui.CreateDCFromHandle(hdc)
        cdc = mdc.CreateCompatibleDC()
        
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mdc, width, height)
        cdc.SelectObject(bmp)
        
        cdc.BitBlt((0, 0), (width, height), mdc, (left, top), win32con.SRCCOPY)
        
        bits = bmp.GetBitmapBits(True)
        frame = np.frombuffer(bits, dtype=np.uint8).reshape((height, width, 4))[:, :, :3].copy()
        
        print(f"  OK: {frame.shape}, mean={frame.mean():.1f}, nonzero={np.count_nonzero(frame)}")
        safe_name = name.replace(" ", "_").replace("(", "").replace(")", "")
        cv2.imwrite(f"reports/debug_detection/gdi_{safe_name}.png", frame)
        
        win32gui.DeleteObject(bmp.GetHandle())
        cdc.DeleteDC()
        mdc.DeleteDC()
        win32gui.ReleaseDC(hdesktop, hdc)
        
    except Exception as exc:
        print(f"  FAILED: {exc}")
        try:
            win32gui.DeleteObject(bmp.GetHandle())
            cdc.DeleteDC()
            mdc.DeleteDC()
            win32gui.ReleaseDC(hdesktop, hdc)
        except:
            pass

# Also try capturing just the center 100x100 of Screen 2
print("\n--- Testing small 100x100 center region of primary monitor ---")
try:
    hdesktop = win32gui.GetDesktopWindow()
    hdc = win32gui.GetWindowDC(hdesktop)
    mdc = win32ui.CreateDCFromHandle(hdc)
    cdc = mdc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mdc, 100, 100)
    cdc.SelectObject(bmp)
    cdc.BitBlt((0, 0), (100, 100), mdc, (910, 490), win32con.SRCCOPY)
    bits = bmp.GetBitmapBits(True)
    frame = np.frombuffer(bits, dtype=np.uint8).reshape((100, 100, 4))[:, :, :3].copy()
    print(f"  OK: {frame.shape}, mean={frame.mean():.1f}")
    win32gui.DeleteObject(bmp.GetHandle())
    cdc.DeleteDC()
    mdc.DeleteDC()
    win32gui.ReleaseDC(hdesktop, hdc)
except Exception as exc:
    print(f"  FAILED: {exc}")
