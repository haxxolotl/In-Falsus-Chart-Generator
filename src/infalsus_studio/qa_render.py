"""In-process rendering proof for our own non-activating QA window only."""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path


def export_window(hwnd, destination):
    from PIL import Image
    user=C.windll.user32;gdi=C.windll.gdi32
    user.GetWindowRect.argtypes=[W.HWND,C.POINTER(W.RECT)]
    user.GetWindowDC.argtypes=[W.HWND];user.GetWindowDC.restype=W.HDC
    user.PrintWindow.argtypes=[W.HWND,W.HDC,W.UINT]
    gdi.CreateCompatibleDC.argtypes=[W.HDC];gdi.CreateCompatibleDC.restype=W.HDC
    gdi.CreateCompatibleBitmap.argtypes=[W.HDC,C.c_int,C.c_int];gdi.CreateCompatibleBitmap.restype=W.HBITMAP
    gdi.SelectObject.argtypes=[W.HDC,W.HGDIOBJ];gdi.SelectObject.restype=W.HGDIOBJ
    gdi.GetDIBits.argtypes=[W.HDC,W.HBITMAP,W.UINT,W.UINT,C.c_void_p,C.c_void_p,W.UINT]
    gdi.DeleteObject.argtypes=[W.HGDIOBJ];gdi.DeleteDC.argtypes=[W.HDC]
    user.ReleaseDC.argtypes=[W.HWND,W.HDC]
    rect=W.RECT();user.GetWindowRect(hwnd,C.byref(rect));w,h=rect.right-rect.left,rect.bottom-rect.top
    dc=user.GetWindowDC(hwnd);memory=gdi.CreateCompatibleDC(dc);bitmap=gdi.CreateCompatibleBitmap(dc,w,h)
    previous=gdi.SelectObject(memory,bitmap)
    try:
        if not user.PrintWindow(hwnd,memory,2):raise RuntimeError('Our QA window could not render its surface')
        gdi.SelectObject(memory,previous)
        header=C.create_string_buffer(40)
        import struct
        header.raw=struct.pack('<IiiHHIIiiII',40,w,-h,1,32,0,w*h*4,0,0,0,0)
        pixels=C.create_string_buffer(w*h*4)
        if gdi.GetDIBits(memory,bitmap,0,h,pixels,header,0)!=h:raise RuntimeError('QA surface read failed')
        output=Path(destination);output.parent.mkdir(parents=True,exist_ok=True)
        Image.frombuffer('RGB',(w,h),pixels,'raw','BGRX',0,1).save(output)
    finally:
        gdi.SelectObject(memory,previous);gdi.DeleteObject(bitmap);gdi.DeleteDC(memory);user.ReleaseDC(hwnd,dc)
