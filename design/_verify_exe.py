# -*- coding: utf-8 -*-
"""新 exe 验收：启动一次，监测启动器/服务/窗口，再关窗验证退出。"""
import ctypes, subprocess, sys, time, urllib.request
from ctypes import wintypes

u = ctypes.windll.user32
u.SetProcessDPIAware()  # 量物理像素

EXE = r"C:\Users\韩凌云\Desktop\novel\dist\书萤\书萤.exe"
APP = "书萤"

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

def find_window():
    """返回 (hwnd, title, rect)；找不到返回 None。"""
    buf = ctypes.create_unicode_buffer(512)
    found = []
    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _l):
        if u.IsWindowVisible(h):
            n = u.GetWindowTextW(h, buf, 512)
            if n and APP in buf.value:
                found.append((h, buf.value))
        return True
    u.EnumWindows(cb, 0)
    for h, t in found:
        r = RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        return h, t, (r.left, r.top, r.right - r.left, r.bottom - r.top)
    return None

def health():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8899/healthz", timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False

print(f"[{time.strftime('%H:%M:%S')}] launching exe ...")
proc = subprocess.Popen([EXE], cwd=r"C:\Users\韩凌云\Desktop\novel\dist\书萤",
                        creationflags=subprocess.CREATE_NO_WINDOW)
print(f"[{time.strftime('%H:%M:%S')}] launcher pid={proc.pid}")

last = None
t0 = time.time()
phase = "run"
while time.time() - t0 < 75:
    alive = proc.poll() is None
    hz = health()
    w = find_window()
    cur = (alive, hz, (round(w[2][2] / 1.5), round(w[2][3] / 1.5)) if w else None,
           (round(w[2][0] / 1.5), round(w[2][1] / 1.5)) if w else None, w[1] if w else None)
    if cur != last:
        print(f"[{time.strftime('%H:%M:%S')}] launcher_alive={cur[0]} health={cur[1]} "
              f"window_dip={cur[2]} pos_dip={cur[3]} title={cur[4]!r}")
        last = cur
    if phase == "run" and time.time() - t0 > 45:
        phase = "close"
        print(f"[{time.strftime('%H:%M:%S')}] --- closing window (WM_CLOSE) ---")
        if w:
            u.PostMessageW(w[0], 0x0010, 0, 0)
        else:
            print("no window to close!")
    time.sleep(0.5)

print(f"[{time.strftime('%H:%M:%S')}] final: launcher_exit_code={proc.poll()} health={health()} window={find_window() is not None}")
print("DONE")
