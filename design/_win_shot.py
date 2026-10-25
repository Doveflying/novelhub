# -*- coding: utf-8 -*-
"""截取书萤应用窗口（PrintWindow + 纯 Python PNG 编码）。"""
import ctypes, sys, time, zlib, struct
from ctypes import wintypes

u = ctypes.windll.user32
g = ctypes.windll.gdi32
u.SetProcessDPIAware()

APP = "书萤"

def find_window():
    buf = ctypes.create_unicode_buffer(512)
    out = []
    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _l):
        if u.IsWindowVisible(h):
            n = u.GetWindowTextW(h, buf, 512)
            if n and APP in buf.value:
                out.append(h)
        return True
    u.EnumWindows(cb, 0)
    return out[0] if out else None

def png(path, w, h, bgra):
    raw = b"".join(b"\x00" + bytes(bgra[y * w * 4:(y + 1) * w * 4]) for y in range(h))
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    data = zlib.compress(raw, 6)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", data))
        f.write(chunk(b"IEND", b""))

hwnd = find_window()
if not hwnd:
    sys.exit("no app window")
r = wintypes.RECT()
u.GetWindowRect(hwnd, ctypes.byref(r))
w, h = r.right - r.left, r.bottom - r.top
print("window", w, "x", h)

hdc = u.GetWindowDC(hwnd)
mdc = g.CreateCompatibleDC(hdc)
bmp = g.CreateCompatibleBitmap(hdc, w, h)
g.SelectObject(mdc, bmp)
PW_RENDERFULLCONTENT = 0x00000002
ok = u.PrintWindow(hwnd, mdc, PW_RENDERFULLCONTENT)
print("PrintWindow:", bool(ok))

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]

bih = BITMAPINFOHEADER()
bih.biSize = ctypes.sizeof(BITMAPINFOHEADER)
bih.biWidth, bih.biHeight, bih.biPlanes, bih.biBitCount = w, -h, 1, 32  # 负高=自上而下
bih.biCompression = 0  # BI_RGB
buf = ctypes.create_string_buffer(w * h * 4)
got = g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bih), 0)
print("GetDIBits:", got)
g.DeleteObject(bmp)
g.DeleteDC(mdc)
u.ReleaseDC(hwnd, hdc)
if got:
    # BGRA -> RGBA
    px = bytearray(buf.raw)
    for i in range(0, len(px), 4):
        px[i], px[i + 2] = px[i + 2], px[i]
    png(sys.argv[1] if len(sys.argv) > 1 else "shot.png", w, h, bytes(px))
    print("saved")
