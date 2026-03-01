import sys
import platform
import traceback
import os
import importlib
import logging
from .cli_color import CliColor as C
from .sys_info import SysInfo as SYS

EXT_NAME = "Stem Separator"
EXT_VERSION = "0.1.0"
PHASE = "Phase 1 - Worker Infrastructure"

# -------------------------------------------------
# BANNER START
# -------------------------------------------------

print()
C.print(f"{C.BOLD}{C.CYAN}{EXT_NAME}{C.RESET}")
C.banner_line()
C.info("Version", f"{EXT_VERSION} ({PHASE})")
C.info("Python", SYS.get_python_version())
C.info("Platform", f"{SYS.get_os()} ({SYS.get_cpu_arch()})")



# -------------------------------------------------
# GPU CHECK
# -------------------------------------------------

try:
    import torch

    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        vram = round(torch.cuda.get_device_properties(0).total_memory / (1024**3), 2)
        C.info("GPU", f"CUDA ✓ ({gpu_name}, {vram} GB)")
    else:
        C.warn("GPU      : CUDA not available")

except Exception:
    C.warn("GPU      : torch not installed")

C.info("Mode", "Subprocess worker")
C.print("")
C.info("Extension", f"{EXT_NAME} v{EXT_VERSION} ({PHASE})")
C.banner_line()

# -------------------------------------------------
# NODE IMPORT
# -------------------------------------------------

C.print("Initializing extension...")

try:
    from .nodes.worker_test import StemWorkerTestNode
    
    C.print("worker_test imported OK")
    NODE_CLASS_MAPPINGS = {
        "StemWorkerTestNode": StemWorkerTestNode,
    }

    NODE_DISPLAY_NAME_MAPPINGS = {
        "StemWorkerTestNode": "Stem Separator: Worker Test (Phase 1)",
    }

except Exception as e:
    C.error("NODE IMPORT FAILED")
    traceback.print_exc()
    NODE_CLASS_MAPPINGS = {}
    NODE_DISPLAY_NAME_MAPPINGS = {}
    raise e

try:
    from .server_routes import register_routes
    C.print("server_routes imported OK")
    register_routes()
    C.print("Routes registered OK")

except Exception:
    C.error("ROUTE IMPORT FAILED")
    traceback.print_exc()
    raise