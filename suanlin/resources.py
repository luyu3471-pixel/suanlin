"""Opt-in worker self-reporting. No LAN scanning, credentials or remote commands."""
import csv
import ctypes
import io
import json
import math
import os
import platform
import shutil
import subprocess
import threading
import time

def command(args):
    return subprocess.check_output(args, timeout=5, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), stderr=subprocess.DEVNULL).decode('utf-8-sig').strip()

def memory():
    if os.name == 'nt':
        class Status(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [(name, ctypes.c_ulonglong) for name in ('total', 'available', 'page_total', 'page_available', 'virtual_total', 'virtual_available', 'extended')]
        status = Status()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return {'memory_total_gib': round(status.total / 2**30, 2), 'memory_available_gib': round(status.available / 2**30, 2)}
    return {}

def cpu_times():
    if os.name != 'nt':
        return None
    idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
    if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return idle.value, kernel.value + user.value

def sanitize(data):
    """Allowlist bounded self-reported fields; reject nonfinite measurements."""
    if not isinstance(data, dict):
        return {}
    result = {}
    for key in ('cpu_name', 'os', 'architecture'):
        if isinstance(data.get(key), str):
            result[key] = data[key][:160]
    for key in ('logical_cores', 'physical_cores', 'memory_total_gib', 'memory_available_gib', 'disk_free_gib', 'cpu_percent'):
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= (100 if key == 'cpu_percent' else 1048576):
            result[key] = value
    gpus = data.get('gpus')
    if isinstance(gpus, list):
        result['gpus'] = []
        for item in gpus[:8]:
            if not isinstance(item, dict):
                continue
            gpu = {'name': str(item.get('name', 'unknown'))[:120]}
            for key in ('memory_total_mib', 'memory_used_mib', 'utilization_percent'):
                value = item.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= (100 if key == 'utilization_percent' else 1048576):
                    gpu[key] = value
            result['gpus'].append(gpu)
    return result

class Monitor:
    def __init__(self, stop):
        self.stop = stop
        self.data = {}
        self.sequence = 0

    def run(self):
        fixed = {'cpu_name': platform.processor(), 'logical_cores': os.cpu_count() or 1, 'os': platform.system()+' '+platform.release(), 'architecture': platform.machine()}
        if os.name == 'nt':
            try:
                rows = json.loads(command(['powershell.exe', '-NoProfile', '-Command', '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; @(Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores) | ConvertTo-Json -Compress']))
                if isinstance(rows, dict): rows = [rows]
                fixed.update(cpu_name='; '.join(r['Name'] for r in rows), physical_cores=sum(r['NumberOfCores'] for r in rows))
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                pass
        previous = cpu_times()
        while not self.stop.is_set():
            measured = dict(fixed, **memory())
            current = cpu_times()
            if current and previous and current[1] > previous[1]:
                measured['cpu_percent'] = round(max(0, min(100, 100*(1-(current[0]-previous[0])/(current[1]-previous[1])))), 1)
            previous = current
            try:
                measured['disk_free_gib'] = round(shutil.disk_usage(os.getcwd()).free / 2**30, 2)
                rows = command(['nvidia-smi', '--query-gpu=name,memory.total,memory.used,utilization.gpu', '--format=csv,noheader,nounits'])
                measured['gpus'] = [{'name': r[0].strip(), 'memory_total_mib': float(r[1]), 'memory_used_mib': float(r[2]), 'utilization_percent': float(r[3])} for r in csv.reader(io.StringIO(rows))]
            except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                pass
            self.data = sanitize(measured)
            self.sequence += 1
            self.stop.wait(5)
