"""Process-held shared runtime guard; exclusive offline maintenance fails fast."""
import os
from pathlib import Path

class FileGuard:
    def __init__(self,path,exclusive=False):
        self.file=None;path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        stream=path.open('a+b')
        try:
            if os.name=='nt':
                import ctypes,msvcrt
                from ctypes import wintypes as w
                class Overlapped(ctypes.Structure):
                    _fields_=[('Internal',ctypes.c_size_t),('InternalHigh',ctypes.c_size_t),('Offset',w.DWORD),('OffsetHigh',w.DWORD),('hEvent',w.HANDLE)]
                self.overlapped=Overlapped();self.handle=w.HANDLE(msvcrt.get_osfhandle(stream.fileno()))
                self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
                self.kernel.LockFileEx.argtypes=[w.HANDLE,w.DWORD,w.DWORD,w.DWORD,w.DWORD,ctypes.POINTER(Overlapped)]
                self.kernel.UnlockFileEx.argtypes=[w.HANDLE,w.DWORD,w.DWORD,w.DWORD,ctypes.POINTER(Overlapped)]
                if not self.kernel.LockFileEx(self.handle,1|(2 if exclusive else 0),0,1,0,ctypes.byref(self.overlapped)):raise OSError('Runtime is active or offline maintenance owns the guard.')
            else:
                import fcntl
                fcntl.flock(stream,(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
            self.file=stream
        except BaseException:stream.close();raise
    def close(self):
        if self.file:
            if os.name=='nt':
                import ctypes
                self.kernel.UnlockFileEx(self.handle,0,1,0,ctypes.byref(self.overlapped))
            self.file.close();self.file=None
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

def runtime_guard(config=None):
    from contextlib import nullcontext
    location=config or os.environ.get('PV_AS2_CONFIG')
    return FileGuard(Path(location).resolve().parent/'runtime.lock') if location else nullcontext()
