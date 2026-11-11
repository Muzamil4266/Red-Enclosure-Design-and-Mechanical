"""
Scrcpy Mirror Win32 Helpers (part 1 of 2)
=========================================
Low-level Windows user32 API bindings for subprocess window embedding
and global mouse wheel hook tracking for touchpad zoom interactions.
"""
import collections
import threading


class _Win32:
    STRIP = 0x80000000 | 0x00C00000 | 0x00040000 | 0x00020000 | 0x00010000 | 0x00080000
    CHILD, VISIBLE = 0x40000000, 0x10000000

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        u = self.u = ctypes.windll.user32
        self.ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        u.EnumWindows.argtypes = [self.ENUM, wintypes.LPARAM]
        u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
        u.SetParent.restype = wintypes.HWND
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowLongW.restype = ctypes.c_long
        u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        u.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.BOOL]
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.IsWindow.argtypes = [wintypes.HWND]
        u.IsWindowVisible.argtypes = [wintypes.HWND]

    def find(self, title):
        hits = []
        def cb(hwnd, lp):
            n = self.u.GetWindowTextLengthW(hwnd)
            if n > 0:
                buf = self.ct.create_unicode_buffer(n + 1)
                self.u.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value == title:
                    hits.append(hwnd)
                    return False
            return True
        self.u.EnumWindows(self.ENUM(cb), 0)
        return hits[0] if hits else None

    def embed(self, hwnd, parent):
        if not parent:
            return False
        res = self.u.SetParent(hwnd, parent)
        if not res:
            return False
        style = (self.u.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF & ~self.STRIP) | self.CHILD | self.VISIBLE
        self.u.SetWindowLongW(hwnd, -16, self.ct.c_long(style).value)
        self.u.ShowWindow(hwnd, 5)
        return True

    def fit(self, hwnd, w, h):
        self.u.MoveWindow(hwnd, 0, 0, max(1, w), max(1, h), True)

    def size(self, hwnd, client=False):
        r = self.wt.RECT()
        (self.u.GetClientRect if client else self.u.GetWindowRect)(hwnd, self.ct.byref(r))
        return r.right - r.left, r.bottom - r.top

    def alive(self, hwnd):
        return bool(self.u.IsWindow(hwnd))

    def ready(self, hwnd):
        if not (self.u.IsWindow(hwnd) and self.u.IsWindowVisible(hwnd)):
            return False
        w, h = self.size(hwnd)
        return w > 100 and h > 100


class _WheelHook:
    def __init__(self):
        self.rect = (0, 0, 0, 0)
        self.frame_hwnd = None
        self.deltas = collections.deque()
        self.ok = False
        self._tid = None
        self._hook = None
        self._ready = threading.Event()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(1.5)

    def _run(self):
        import ctypes
        from ctypes import wintypes
        u, k = ctypes.windll.user32, ctypes.windll.kernel32

        class MSLL(ctypes.Structure):
            _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]
        PROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, PROC, wintypes.HINSTANCE, wintypes.DWORD]
        u.SetWindowsHookExW.restype = wintypes.HHOOK
        u.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        u.CallNextHookEx.restype = ctypes.c_ssize_t
        u.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        u.WindowFromPoint.argtypes = [wintypes.POINT]
        u.WindowFromPoint.restype = wintypes.HWND
        u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetAncestor.restype = wintypes.HWND
        u.GetAsyncKeyState.restype = ctypes.c_short
        u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        k.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        k.GetModuleHandleW.restype = wintypes.HMODULE

        def proc(n, w, l):
            try:
                if n >= 0 and w == 0x020A and (u.GetAsyncKeyState(0x11) & 0x8000):
                    ms = ctypes.cast(l, ctypes.POINTER(MSLL)).contents
                    x0, y0, x1, y1 = self.rect
                    if x0 <= ms.pt.x < x1 and y0 <= ms.pt.y < y1 and self.frame_hwnd:
                        ours = u.GetAncestor(self.frame_hwnd, 2)
                        under = u.GetAncestor(u.WindowFromPoint(ms.pt), 2)
                        if not ours or ours != under:
                            return u.CallNextHookEx(None, n, w, l)
                        self.deltas.append(ctypes.c_short((ms.mouseData >> 16) & 0xFFFF).value)
                        return 1
            except Exception:
                pass
            return u.CallNextHookEx(None, n, w, l)

        self._proc = PROC(proc)
        self._hook = u.SetWindowsHookExW(14, self._proc, k.GetModuleHandleW(None), 0)
        self._tid = k.GetCurrentThreadId()
        self.ok = bool(self._hook)
        self._ready.set()
        if not self._hook:
            return
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))
        u.UnhookWindowsHookEx(self._hook)

    def stop(self):
        if self._tid:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)
            self._tid = None