import ctypes
from ctypes import wintypes
import json
from pathlib import Path

def selected_environment(pid):
    """Inspect only a selected local PV1 worker (64-bit Windows)."""
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError('PV1 process verification requires 64-bit Python')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    ntdll = ctypes.WinDLL('ntdll')
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_uint, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
    handle = kernel.OpenProcess(0x410, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    def read(address, count):
        buffer = ctypes.create_string_buffer(count)
        received = ctypes.c_size_t()
        ok = kernel.ReadProcessMemory(handle, address, buffer, count, ctypes.byref(received))
        if not ok and not received.value:
            raise ctypes.WinError(ctypes.get_last_error())
        return buffer.raw[:received.value]
    try:
        basic = ctypes.create_string_buffer(48)
        status = ntdll.NtQueryInformationProcess(handle, 0, basic, len(basic), None)
        if status:
            raise RuntimeError(f'NtQueryInformationProcess status {status}')
        peb = int.from_bytes(basic.raw[8:16], 'little')
        parameters = int.from_bytes(read(peb + 0x20, 8), 'little')
        current = read(parameters + 0x38, 16)
        current_directory = read(int.from_bytes(current[8:16], 'little'),
                                 int.from_bytes(current[:2], 'little')).decode('utf-16-le')
        address = int.from_bytes(read(parameters + 0x80, 8), 'little')
        data = b''
        for offset in range(0, 262144, 4096):
            data += read(address + offset, 4096)
            decoded = data.decode('utf-16-le', errors='ignore')
            if '\0\0' in decoded:
                break
        else:
            raise RuntimeError('Environment terminator not found')
        allowed = {'DATABASE_URL', 'PV_STORAGE_ROOT', 'PV_APP_MODE', 'PV_USER_SWITCH_ENABLED', 'PV_ALLOW_QUERY_USER_SWITCH', 'PV_AUTH_COOKIE_SECURE',
                   'PV_AUTH_COOKIE_NAME', 'PV_INSTANCE_LABEL', 'PV_AS2_CONFIG', 'PV_RESTORE_PATH_MAP'}
        values = {k: v for item in decoded.split('\0\0', 1)[0].split('\0') if '=' in item
                for k, v in [item.split('=', 1)] if k in allowed}
        return current_directory, values
    finally:
        kernel.CloseHandle(handle)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Verify the actual local PV1 listener configuration; never change or terminate it.')
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--storage', type=Path)
    args = parser.parse_args()
    try:
        directory, values = selected_environment(args.pid)
        root = args.root.resolve()
        database = args.database.resolve() if args.database else root / 'pv_system.db'
        storage = args.storage.resolve() if args.storage else root / 'uploads'
        if Path(directory).resolve() != root:
            raise ValueError('Listener working directory differs from this installation')
        url = values.get('DATABASE_URL', '')
        if not url.startswith('sqlite:///') or Path(url[10:]).resolve() != database:
            raise ValueError('Listener database differs from the main database')
        if not values.get('PV_STORAGE_ROOT') or Path(values['PV_STORAGE_ROOT']).resolve() != storage:
            raise ValueError('Listener file storage differs from the main storage')
        for name, expected in [('PV_APP_MODE', 'prod'), ('PV_USER_SWITCH_ENABLED', 'false'),
                               ('PV_ALLOW_QUERY_USER_SWITCH', 'false'), ('PV_AUTH_COOKIE_SECURE', 'false')]:
            if values.get(name) != expected:
                raise ValueError('Listener has different application settings: ' + name)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, str(exc) + '\n')
    print(json.dumps({'verified_pid': args.pid, 'project': str(root),
                      'database': url, 'storage': values['PV_STORAGE_ROOT']}))
