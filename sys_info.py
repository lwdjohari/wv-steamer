import sys
import platform
import os

# -------------------------------------------------
# Platform and System Information
# -------------------------------------------------

class SysInfo:
    
    @staticmethod
    def is_windows():
        return sys.platform.startswith("win")
    
    @staticmethod
    def is_linux():
        return sys.platform.startswith("linux")
    
    @staticmethod
    def is_mac():        
        return sys.platform.startswith("darwin")
    
    @staticmethod
    def get_cpu_arch():
        arch = platform.machine().lower()
        if arch in ("x86_64", "amd64"):
            return "x86_64"
        elif arch in ("aarch64", "arm64"):
            return "arm64"
        else:
            return "unknown"
        
    @staticmethod
    def get_os():
        if SysInfo.is_windows():
            return "Windows"
        elif SysInfo.is_linux():
            return "Linux"
        elif SysInfo.is_mac():
            return "macOS"
        else:
            return "Unknown"
        
    def get_python_version():
        return platform.python_version()
    
    def get_comfyui_version():
        try:
            import comfyui
            return comfyui.__version__
        except ImportError:
            return "Unknown"

    def get_extension_info():
        return f"{EXT_NAME} v{EXT_VERSION} ({PHASE})"
    
    def get_full_sys_info():
        return (
            f"OS: {SysInfo.get_os()} ({SysInfo.get_cpu_arch()})\n"
            f"Python: {SysInfo.get_python_version()}\n"
            f"ComfyUI: {SysInfo.get_comfyui_version()}\n"
            f"Extension: {SysInfo.get_extension_info()}"
        )
    
    @staticmethod
    def supports_color():
        if SysInfo.is_windows():
            return os.getenv("ANSICON") is not None or "WT_SESSION" in os.environ
        return sys.stdout.isatty()
