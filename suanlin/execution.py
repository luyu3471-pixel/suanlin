"""Worker-local progress and cooperative cancellation, including process workers."""
import json
import ctypes
import os
import time
from pathlib import Path
from .inference import run_task

def resident_mib():
    if os.name!='nt': return None
    class Counters(ctypes.Structure):
        _fields_=[('cb',ctypes.c_ulong),('faults',ctypes.c_ulong)]+[(k,ctypes.c_size_t) for k in
            ('peak','working','quota_peak_paged','quota_paged','quota_peak_nonpaged','quota_nonpaged','pagefile','peak_pagefile')]
    value=Counters();value.cb=ctypes.sizeof(value)
    ctypes.windll.kernel32.GetCurrentProcess.restype=ctypes.c_void_p
    if ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(ctypes.windll.kernel32.GetCurrentProcess()),ctypes.byref(value),value.cb):
        return round(value.working/2**20,2)
    return None

def execute(payload, progress_path, cancel_path):
    last = 0
    started = time.monotonic()
    def report(stage, done, total):
        nonlocal last
        now = time.monotonic()
        if Path(cancel_path).exists():
            raise RuntimeError('task_cancelled')
        if now-last<.5 and done!=total: return
        last=now
        path=Path(progress_path)
        temporary=path.with_suffix('.writing')
        try:
            temporary.write_text(json.dumps({'stage':stage,'done':done,'total':total,
                                            'elapsed_ms':round((now-started)*1000),'memory_mib':resident_mib()}),encoding='utf-8')
            temporary.replace(path)
        except OSError:
            pass  # A transient Windows reader lock must not fail business work.
    return run_task(payload, report)
