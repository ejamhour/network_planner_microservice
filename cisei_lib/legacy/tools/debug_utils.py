import builtins
import inspect
import os
import sys
import time
import tracemalloc
import time


_debug_cache = {}

class DebugWriter:
    def write(self, message):
        if message.strip():  # Ignore blank lines
            frame = inspect.stack()[1]
            filename = os.path.basename(frame.filename)
            lineno = frame.lineno
            sys.__stdout__.write(f"[{filename}:{lineno}] {message}")
    
    def flush(self):
        pass  # Needed for compatibility
   
def patch_sysout():
    sys.stdout = DebugWriter()

def patch_print():
    _original_print = builtins.print
    def debug_print(*args, **kwargs):
        frame = inspect.stack()[1]
        filename = os.path.basename(frame.filename)
        lineno = frame.lineno
        _original_print(f"[{filename}:{lineno}]", *args, **kwargs)
    builtins.print = debug_print

def debug_vars(*args, label=None, maxlen=300):
    import inspect, json
    frame = inspect.currentframe().f_back
    out = {}
    for name in args:
        val = frame.f_locals.get(name, "<undefined>")
        _debug_cache[name] = val
        try:
            val_str = json.dumps(val, indent=2, default=str)
        except Exception:
            val_str = str(val)
        if len(val_str) > maxlen:
            val_str = val_str[:maxlen] + " ... [truncated]"
        out[name] = val_str

    if label:
        print(f"\n===== [{label}] =====")
    for k, v in out.items():
        print(f"{k} = {v}\n")

# debug_vars('mygraph', 'conf', 'some_flag', maxlen=500)

def debug_page(name, page=0, lines=30):
    import json
    val = _debug_cache.get(name, "<not captured>")
    try:
        val_str = json.dumps(val, indent=2, default=str)
    except Exception:
        val_str = str(val)

    chunks = val_str.splitlines()
    start = page * lines
    end = start + lines
    if start >= len(chunks):
        print(f"[show] Page {page} out of range. Total lines: {len(chunks)}")
        return

    print(f"\n--- {name} | page {page} ({start}-{end}) ---")
    print("\n".join(chunks[start:end]))

# show(name, page=0, lines=30) 

def clear_debug_cache():
    _debug_cache.clear()
    print("[debug cache cleared]")


def benchmark(func, label='benchmark'):
    tracemalloc.start()
    t0 = time.perf_counter()

    func()

    t1 = time.perf_counter()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(f"[{label}] Time: {t1 - t0:.4f} s")
    print(f"[{label}] Tracemalloc Current: {current/1024**2:.2f} MB")
    print(f"[{label}] Tracemalloc Peak: {peak/1024**2:.2f} MB")
