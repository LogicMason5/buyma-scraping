"""Wait for operator confirmation without requiring a console stdin.

Frozen (windowed) builds have no console. Cookie helpers must show a small
always-on-top panel that does NOT cover Chrome, so the user can finish WAF
and login first.
"""

from __future__ import annotations

import os
import sys


def _win32_message_box(title: str, message: str, *, style: int) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        MB_TOPMOST = 0x00040000
        MB_SETFOREGROUND = 0x00010000
        IDOK = 1

        # Do not use SYSTEMMODAL: it sits over Chrome and blocks login.
        flags = style | MB_TOPMOST | MB_SETFOREGROUND
        result = ctypes.windll.user32.MessageBoxW(0, message, title, flags)
        if style & 0x00000001:  # MB_OKCANCEL
            return result == IDOK
        return True
    except Exception:  # noqa: BLE001
        return False


def _corner_confirm(title: str, message: str) -> bool | None:
    """Small top-right dialog. Returns None if Tk is unavailable."""
    try:
        import tkinter as tk
    except Exception:  # noqa: BLE001
        return None

    result = {"ok": False}

    try:
        root = tk.Tk()
    except Exception:  # noqa: BLE001
        return None

    root.title(title)
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except Exception:  # noqa: BLE001
        pass
    root.configure(bg="#1e1e1e")

    width, height = 420, 240
    try:
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
    except Exception:  # noqa: BLE001
        sw, sh = 1280, 720
    x = max(20, sw - width - 36)
    y = 48
    if sh > 0 and y + height > sh - 80:
        y = max(20, sh - height - 96)
    root.geometry(f"{width}x{height}+{x}+{y}")

    tk.Label(
        root,
        text=title,
        fg="#f3f3f3",
        bg="#1e1e1e",
        font=("Segoe UI", 11, "bold"),
        wraplength=380,
        justify="left",
        anchor="w",
    ).pack(fill="x", padx=16, pady=(14, 6))
    tk.Label(
        root,
        text=message,
        fg="#d6d6d6",
        bg="#1e1e1e",
        font=("Segoe UI", 9),
        wraplength=380,
        justify="left",
        anchor="w",
    ).pack(fill="both", expand=True, padx=16, pady=(0, 10))

    buttons = tk.Frame(root, bg="#1e1e1e")
    buttons.pack(fill="x", padx=12, pady=(0, 12))

    def _ok() -> None:
        result["ok"] = True
        root.destroy()

    def _cancel() -> None:
        result["ok"] = False
        root.destroy()

    tk.Button(
        buttons,
        text="ログイン完了 → 保存",
        command=_ok,
        bg="#2563eb",
        fg="white",
        activebackground="#1d4ed8",
        activeforeground="white",
        relief="flat",
        padx=12,
        pady=6,
        font=("Segoe UI", 9, "bold"),
    ).pack(side="right", padx=4)
    tk.Button(
        buttons,
        text="キャンセル",
        command=_cancel,
        bg="#3f3f3f",
        fg="white",
        activebackground="#525252",
        activeforeground="white",
        relief="flat",
        padx=12,
        pady=6,
        font=("Segoe UI", 9),
    ).pack(side="right", padx=4)

    root.protocol("WM_DELETE_WINDOW", _cancel)

    def _keep_on_top() -> None:
        try:
            if root.winfo_exists():
                root.attributes("-topmost", True)
                root.after(2500, _keep_on_top)
        except Exception:  # noqa: BLE001
            pass

    root.after(400, _keep_on_top)
    try:
        root.lift()
    except Exception:  # noqa: BLE001
        pass
    root.mainloop()
    return bool(result["ok"])


def wait_for_user_ready(title: str, message: str) -> bool:
    """Block until the user confirms. Returns False if cancelled."""
    if (os.environ.get("EC_BUYMA_AUTO_CONFIRM") or "").strip() in {"1", "true", "yes"}:
        print(f"[auto-confirm] {title}: {message}", flush=True)
        return True

    corner = _corner_confirm(title, message)
    if corner is not None:
        return corner

    if _win32_message_box(title, message, style=0x00000001 | 0x00000030):
        return True

    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:  # noqa: BLE001
            pass
        root.update()
        ok = bool(messagebox.askokcancel(title, message, parent=root))
        root.destroy()
        return ok
    except Exception as exc:  # noqa: BLE001
        print(f"(dialog unavailable: {exc})", flush=True)

    if sys.stdin is not None and getattr(sys.stdin, "isatty", lambda: False)():
        try:
            input(f"{message}\nPress Enter to continue (Ctrl+C to cancel)… ")
            return True
        except (EOFError, KeyboardInterrupt):
            return False

    print("No UI and no console — cannot wait for confirmation.", flush=True)
    return False


def notify_user(title: str, message: str) -> None:
    """Show an info dialog."""
    if (os.environ.get("EC_BUYMA_AUTO_CONFIRM") or "").strip() in {"1", "true", "yes"}:
        print(f"[notify] {title}: {message}", flush=True)
        return

    if _win32_message_box(title, message, style=0x00000040):
        return

    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:  # noqa: BLE001
            pass
        root.update()
        messagebox.showinfo(title, message, parent=root)
        root.destroy()
    except Exception as exc:  # noqa: BLE001
        print(f"{title}: {message} (dialog unavailable: {exc})", flush=True)
