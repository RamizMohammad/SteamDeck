import customtkinter as ctk
import json
import os
import random
import threading
import subprocess
import asyncio
import websockets
from PIL import Image
from tkinter import filedialog, messagebox
from datetime import datetime
import time
import winreg
import sys
import pystray
from pystray import MenuItem as item

# ============================
# CONFIGURATION
# ============================
APP_NAME = "Linkium"
WINDOW_SIZE = "1100x700"

# ✅ Use AppData path for writable files
USER_DATA_DIR = os.path.join(os.getenv("APPDATA"), "Linkium")
os.makedirs(USER_DATA_DIR, exist_ok=True)

APP_DATA_FILE = os.path.join(USER_DATA_DIR, "apps_data.json")
CODE_FILE = os.path.join(USER_DATA_DIR, "receiver_code.json")
SETTINGS_FILE = os.path.join(USER_DATA_DIR, "settings.json")

SERVER_URL = "wss://steamdeck.onrender.com/ws"

# ============================
# THEME COLORS
# ============================
PRIMARY_COLOR = "#00B4FF"
PRIMARY_DARK = "#0082C1"
GRADIENT_TOP = "#13161A"
GRADIENT_MID = "#0F1215"
GRADIENT_BOTTOM = "#090B0E"
CARD_BG = "#181818"
CARD_HOVER = "#202225"
CARD_SELECTED = "#004E73"
TEXT_MAIN = "#F0F0F0"
TEXT_SUBTLE = "#A5A5A5"
ACCENT_GREEN = "#2ECC71"
ACCENT_RED = "#E74C3C"

FONT_TITLE = ("Segoe UI", 22, "bold")
FONT_BOLD = ("Segoe UI", 15, "bold")
FONT_NORMAL = ("Segoe UI", 13)

# ============================
# TRAY ICON MANAGEMENT
# ============================
class TrayManager:
    def __init__(self, app):
        self.app = app
        self.icon = None

    def create_tray_icon(self):
        # Your tray icon image (use your own icon file here)
        image = Image.open("assets/RLogo.png")
        menu = (
            item('Open Linkium', self.show_app),
            item('Exit', self.quit_app)
        )
        self.icon = pystray.Icon("Linkium", image, "Linkium", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    def show_app(self):
        # Bring window to front
        self.app.deiconify()
        self.app.lift()
        self.app.focus_force()

    def quit_app(self):
        # Clean shutdown
        if self.icon:
            self.icon.stop()
        self.app.on_closing()

# ============================
# UTILITIES
# ============================
def load_apps_data():
    if os.path.exists(APP_DATA_FILE):
        try:
            with open(APP_DATA_FILE, "r") as f:
                data = json.load(f)
                apps = data.get("apps", {})
                if isinstance(apps, list):
                    fixed = {a["name"]: a["path"] for a in apps if "name" in a and "path" in a}
                    save_apps_data(fixed)
                    return fixed
                elif isinstance(apps, dict):
                    return apps
        except Exception as e:
            print("Error loading apps:", e)
    return {}

def ensure_default_files():
    # Ensure apps_data.json
    if not os.path.exists(APP_DATA_FILE):
        with open(APP_DATA_FILE, "w") as f:
            json.dump({"apps": {}}, f, indent=4)

    # Ensure receiver_code.json
    if not os.path.exists(CODE_FILE):
        code = str(random.randint(10**9, (10**10) - 1))
        json.dump({"code": code}, open(CODE_FILE, "w"))

    # Ensure settings.json
    if not os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "w") as f:
            json.dump({"startup_enabled": False}, f, indent=4)

def save_apps_data(apps):
    try:
        with open(APP_DATA_FILE, "w") as f:
            json.dump({"apps": apps}, f, indent=4)
    except Exception as e:
        print("Error saving apps:", e)

def load_or_create_code():
    if os.path.exists(CODE_FILE):
        try:
            return json.load(open(CODE_FILE))["code"]
        except Exception:
            pass
    code = str(random.randint(10**9, (10**10) - 1))
    json.dump({"code": code}, open(CODE_FILE, "w"))
    return code

def regenerate_code():
    code = str(random.randint(10**9, (10**10) - 1))
    json.dump({"code": code}, open(CODE_FILE, "w"))
    return code

# ============================
# SETTINGS (Startup Toggle)
# ============================
def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"startup_enabled": False}

def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=4)

def set_startup(enabled):
    app_name = APP_NAME
    exe_path = os.path.abspath(sys.argv[0])
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                            winreg.KEY_ALL_ACCESS) as key:
            if enabled:
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, f'"{exe_path}" --silent')
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        print(f"[ERROR] Startup registry error: {e}")
        return False

# ============================
# RECEIVER THREAD
# ============================
class ReceiverThread(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.running = True
        self.connection_status = "Disconnected"
        self.code = load_or_create_code()
        self.reconnect_event = threading.Event()
        self.lock = threading.Lock()

    async def connect_ws(self):
        try:
            async with websockets.connect(SERVER_URL) as ws:
                await ws.send(json.dumps({"role": "receiver", "code": self.code}))
                self.connection_status = "Connected"
                self.app.update_status("Connected")
                self.app.log(f"✅ Connected with code: {self.code}", "ok")
                while self.running:
                    if self.reconnect_event.is_set():
                        self.app.log("🔄 Reconnect requested — closing current connection...", "info")
                        break
                    try:
                        recv_task = asyncio.create_task(ws.recv())
                        done, _ = await asyncio.wait({recv_task}, timeout=1.0)
                        if recv_task in done:
                            msg = recv_task.result()
                            await self.handle_message(ws, json.loads(msg))
                        else:
                            recv_task.cancel()
                    except websockets.exceptions.ConnectionClosed:
                        self.app.log("⚠️ Server closed connection", "error")
                        break
                    except Exception as e:
                        self.app.log(f"⚠️ Error: {e}", "error")
                        await asyncio.sleep(0.5)
        except Exception as e:
            self.app.log(f"⚠️ Connection Error: {e}", "error")
            self.connection_status = "Disconnected"
            self.app.update_status("Disconnected")

    async def handle_message(self, ws, data):
        cmd = data.get("command")
        latest_programs = load_apps_data()

        if cmd == "get_programs":
            await ws.send(json.dumps({"programs": [{"name": n, "path": p} for n, p in latest_programs.items()]}))
            self.app.log("📤 Sent latest program list to server", "info")

        elif cmd == "open":
            prog = data.get("program")
            if prog in latest_programs:
                try:
                    subprocess.Popen(latest_programs[prog], shell=True)
                    self.app.log(f"🚀 Opened {prog}", "ok")
                except Exception as e:
                    self.app.log(f"❌ Failed to open {prog}: {e}", "error")
            else:
                self.app.log(f"❌ Unknown program: {prog}", "error")

        elif cmd == "regenerate_code":
            new_code = regenerate_code()
            with self.lock:
                self.code = new_code
            await ws.send(json.dumps({"new_code": new_code}))
            self.app.update_code(new_code)
            self.app.log(f"🔁 Code regenerated remotely: {new_code}", "info")
            self.reconnect_event.set()

    def run(self):
        asyncio.run(self.run_loop())

    async def run_loop(self):
        while self.running:
            self.reconnect_event.clear()
            await self.connect_ws()
            if not self.running:
                break
            if self.reconnect_event.is_set():
                self.app.log("🔁 Reconnecting with updated code...", "info")
                await asyncio.sleep(0.3)
                continue
            self.app.log("⏳ Disconnected — retrying in 3s...", "info")
            self.connection_status = "Disconnected"
            self.app.update_status("Disconnected")
            await asyncio.sleep(3)

    def stop(self):
        self.running = False
        self.reconnect_event.set()

    def trigger_reconnect(self, new_code):
        with self.lock:
            self.code = new_code
        self.reconnect_event.set()

# ==========================
# Splash Screen
# =========================

def show_splash():
    import customtkinter as ctk
    from PIL import Image
    import os, sys

    splash = ctk.CTk()
    splash.geometry("400x300")
    splash.title("")
    splash.resizable(False, False)

    # ✅ Universal base path detection
    if getattr(sys, 'frozen', False):
        base_path = os.path.dirname(sys.executable)
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))

    img_path = os.path.join(base_path, "assets", "RLogo.png")

    try:
        img = ctk.CTkImage(Image.open(img_path), size=(100, 100))
        label = ctk.CTkLabel(splash, image=img, text="")
        label.pack(pady=40)
    except Exception as e:
        print(f"[Splash] Failed to load image: {e}")

    ctk.CTkLabel(splash, text="Linkium.space", font=("Segoe UI", 16, "bold")).pack()
    splash.after(2500, splash.destroy)
    splash.mainloop()

# ============================
# MAIN APPLICATION
# ============================
class SteamDeckApp(ctk.CTk):
    def __init__(self, silent=False):
        super().__init__()

        # 🧠 Tray setup
        self.tray = TrayManager(self)
        if silent:
            self.withdraw()
            self.after(2000, self.tray.create_tray_icon)
        else:
            self.tray.create_tray_icon()

        # Rest of your setup
        self.title(APP_NAME)
        self.geometry(WINDOW_SIZE)
        ctk.set_appearance_mode("Dark")
        self.resizable(False, False)

        # Background
        self.bg_canvas = ctk.CTkCanvas(self, highlightthickness=0, bd=0)
        self.bg_canvas.pack(fill="both", expand=True)
        self._draw_gradient()

        # Container
        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.place(relwidth=1, relheight=1)

        # Load settings
        self.settings = load_settings()

        # State
        self.receiver_thread = None
        self.programs = load_apps_data()
        self.selected_program = None
        self.pair_code = load_or_create_code()

        # Layout
        self.container.grid_columnconfigure(1, weight=1)
        self.container.grid_rowconfigure(1, weight=1)

        # UI
        self.create_topbar(self.container)
        self.create_sidebar(self.container)
        self.create_mainpanel(self.container)
        self.create_logger(self.container)

        # Apply startup switch state
        if self.settings.get("startup_enabled"):
            self.startup_switch.select()
        else:
            self.startup_switch.deselect()

        # Start receiver
        self.start_receiver_thread()

    def _draw_gradient(self):
        width, height = 1100, 700
        steps = 100
        for i in range(steps):
            r = int(19 + (9 - 19) * i / steps)
            g = int(22 + (11 - 22) * i / steps)
            b = int(26 + (14 - 26) * i / steps)
            color = f"#{r:02x}{g:02x}{b:02x}"
            y1 = int(height * i / steps)
            y2 = int(height * (i + 1) / steps)
            self.bg_canvas.create_rectangle(0, y1, width, y2, outline="", fill=color)

    # ======================
    # UI
    # ======================
    def create_topbar(self, parent):
        frame = ctk.CTkFrame(parent, height=65, fg_color=GRADIENT_TOP)
        frame.grid(row=0, column=0, columnspan=2, sticky="ew")

        # logo_image = ctk.CTkImage(Image.open("assets/Rlogo.png"), size=(40, 40))
        # logo_label = ctk.CTkLabel(frame, image=logo_image, text="")
        # logo_label.pack(side="left", padx=(20, 10))

        self.code_label = ctk.CTkLabel(frame, text=f"🔑 {self.pair_code}", font=FONT_BOLD, text_color=TEXT_MAIN)
        self.code_label.pack(side="left", padx=(30, 15))

        ctk.CTkButton(frame, text="📋", width=40, height=35,
                      fg_color="#2A2A2A", hover_color="#3A3A3A",
                      corner_radius=12, command=self.copy_code).pack(side="left")

        ctk.CTkButton(frame, text="Regenerate Code", width=160, height=35,
                      fg_color=PRIMARY_COLOR, hover_color=PRIMARY_DARK,
                      corner_radius=12, command=self.regenerate_code_ui).pack(side="left", padx=(20, 10))

        # Startup toggle
        self.startup_switch = ctk.CTkSwitch(frame, text="Launch on Startup",
                                            onvalue=True, offvalue=False,
                                            command=self.toggle_startup)
        self.startup_switch.pack(side="right", padx=(0, 20))

        # Status
        self.status_label = ctk.CTkLabel(frame, text="🔴 Disconnected", font=FONT_NORMAL, text_color=ACCENT_RED)
        self.status_label.pack(side="right", padx=10)

    def toggle_startup(self):
        enabled = bool(self.startup_switch.get())
        success = set_startup(enabled)
        if success:
            self.settings["startup_enabled"] = enabled
            save_settings(self.settings)
            state = "enabled" if enabled else "disabled"
            self.log(f"⚙️ Startup launch {state}.", "info")
        else:
            messagebox.showerror("Error", "Failed to modify startup setting.")

    def create_sidebar(self, parent):
        self.sidebar = ctk.CTkFrame(parent, width=260, fg_color=GRADIENT_MID)
        self.sidebar.grid(row=1, column=0, sticky="ns")
        ctk.CTkLabel(self.sidebar, text="📂 Installed Apps", font=FONT_BOLD, text_color=TEXT_MAIN).pack(pady=15)
        self.app_list = ctk.CTkScrollableFrame(self.sidebar, fg_color="transparent", width=250)
        self.app_list.pack(fill="both", expand=True, padx=10)
        ctk.CTkButton(self.sidebar, text="+ Add App", fg_color=PRIMARY_COLOR, hover_color=PRIMARY_DARK,
                      command=self.add_app_dialog).pack(fill="x", padx=20, pady=15)
        self.refresh_sidebar()

    def create_mainpanel(self, parent):
        self.main_panel = ctk.CTkFrame(parent, fg_color=GRADIENT_BOTTOM, corner_radius=15)
        self.main_panel.grid(row=1, column=1, sticky="nsew", padx=20, pady=(20, 5))
        self.app_label = ctk.CTkLabel(self.main_panel, text="Select an Application", font=FONT_TITLE, text_color=TEXT_MAIN)
        self.app_label.pack(pady=20)
        self.path_label = ctk.CTkLabel(self.main_panel, text="Path: None", font=FONT_NORMAL, text_color=TEXT_SUBTLE)
        self.path_label.pack(pady=(0, 10))
        btn_frame = ctk.CTkFrame(self.main_panel, fg_color="transparent")
        btn_frame.pack(pady=20)
        self.open_btn = ctk.CTkButton(btn_frame, text="Open", state="disabled", command=self.launch_app)
        self.edit_btn = ctk.CTkButton(btn_frame, text="Edit", state="disabled", command=self.edit_app)
        self.del_btn = ctk.CTkButton(btn_frame, text="Delete", state="disabled",
                                     fg_color=ACCENT_RED, hover_color="#C0392B", command=self.delete_app)
        self.open_btn.pack(side="left", padx=10)
        self.edit_btn.pack(side="left", padx=10)
        self.del_btn.pack(side="left", padx=10)

    def create_logger(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=GRADIENT_MID, height=150)
        frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=15, pady=(5, 15))
        self.log_box = ctk.CTkTextbox(frame, height=140, width=950)
        self.log_box.pack(fill="both", padx=10, pady=10)
        self.log_box.insert("end", "🧠 System Initialized...\n")
        self.log_box.configure(state="disabled")

    # ======================
    # APP LOGIC
    # ======================
    def refresh_sidebar(self):
        for w in self.app_list.winfo_children():
            w.destroy()
        for name, path in self.programs.items():
            card = ctk.CTkFrame(self.app_list, fg_color=CARD_BG, corner_radius=8)
            card.pack(fill="x", pady=3, padx=5)
            label = ctk.CTkLabel(card, text=name, anchor="w", font=FONT_NORMAL)
            label.pack(side="left", padx=10, fill="x", expand=True)
            def on_enter(e, w=card): w.configure(fg_color=CARD_HOVER)
            def on_leave(e, w=card):
                if name != self.selected_program: w.configure(fg_color=CARD_BG)
            card.bind("<Enter>", on_enter)
            card.bind("<Leave>", on_leave)
            card.bind("<Button-1>", lambda e, n=name, w=card: self.select_app(n, w))
            label.bind("<Button-1>", lambda e, n=name, w=card: self.select_app(n, w))

    def select_app(self, name, widget):
        self.selected_program = name
        self.app_label.configure(text=name)
        self.path_label.configure(text=f"Path: {self.programs[name]}")
        self.open_btn.configure(state="normal")
        self.edit_btn.configure(state="normal")
        self.del_btn.configure(state="normal")
        for child in self.app_list.winfo_children():
            child.configure(fg_color=CARD_BG)
        widget.configure(fg_color=CARD_SELECTED)

    # ======================
    # FILE OPS
    # ======================
    def add_app_dialog(self): self.show_app_dialog("add")
    def edit_app(self, name=None):
        if not name and not self.selected_program: return
        self.show_app_dialog("edit", name or self.selected_program)

    def show_app_dialog(self, mode, name=None):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Add/Edit Application")
        dialog.geometry("400x300")
        dialog.grab_set()
        ctk.CTkLabel(dialog, text="Application Name:", font=FONT_NORMAL).pack(pady=(15, 5))
        name_entry = ctk.CTkEntry(dialog, width=300)
        name_entry.pack()
        ctk.CTkLabel(dialog, text="Application Path:", font=FONT_NORMAL).pack(pady=(10, 5))
        path_entry = ctk.CTkEntry(dialog, width=260)
        path_entry.pack()
        def browse():
            file = filedialog.askopenfilename(filetypes=[("Executables", "*.exe"), ("All Files", "*.*")])
            if file:
                path_entry.delete(0, "end")
                path_entry.insert(0, file)
        ctk.CTkButton(dialog, text="Browse", command=browse, width=60).pack(pady=10)
        if mode == "edit" and name:
            name_entry.insert(0, name)
            path_entry.insert(0, self.programs[name])
        def save():
            n = name_entry.get().strip()
            p = path_entry.get().strip()
            if not n or not p:
                messagebox.showwarning("Error", "Both fields required", parent=dialog)
                return
            self.programs[n] = p
            save_apps_data(self.programs)
            self.refresh_sidebar()
            dialog.destroy()
            self.log(f"💾 Saved app: {n}", "ok")
        ctk.CTkButton(dialog, text="Save", fg_color=PRIMARY_COLOR, command=save).pack(pady=10)

    def delete_app(self):
        name = self.selected_program
        if not name:
            return
        if messagebox.askyesno("Confirm Delete", f"Delete {name}?"):
            self.programs.pop(name, None)
            save_apps_data(self.programs)
            self.refresh_sidebar()
            self.app_label.configure(text="Select an Application")
            self.path_label.configure(text="Path: None")
            self.open_btn.configure(state="disabled")
            self.edit_btn.configure(state="disabled")
            self.del_btn.configure(state="disabled")
            self.log(f"🗑️ Deleted app: {name}", "info")

    def launch_app(self):
        name = self.selected_program
        if not name:
            return
        path = self.programs[name]
        try:
            subprocess.Popen(path, shell=True)
            self.log(f"🚀 Opened {name}", "ok")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open {name}\n{e}")

    # ======================
    # STATUS & LOGGING
    # ======================
    def copy_code(self):
        self.clipboard_clear()
        self.clipboard_append(self.pair_code)
        self.log("📋 Code copied to clipboard!", "info")

    def update_code(self, new_code):
        # called when code changes (UI or remote). Notify receiver thread to reconnect.
        self.pair_code = new_code
        self.code_label.configure(text=f"🔑 {new_code}")
        if self.receiver_thread:
            self.receiver_thread.trigger_reconnect(new_code)

    def regenerate_code_ui(self):
        new_code = regenerate_code()
        self.update_code(new_code)
        self.log(f"🔁 Code regenerated manually: {new_code}", "info")

    def update_status(self, status):
        if status == "Connected":
            self.status_label.configure(text="🟢 Connected", text_color=ACCENT_GREEN)
        elif status == "Disconnected":
            self.status_label.configure(text="🔴 Disconnected", text_color=ACCENT_RED)

    def log(self, msg, level="info"):
        self.log_box.configure(state="normal")
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = {"info": "💬", "ok": "✅", "error": "⚠️"}.get(level, "💬")
        self.log_box.insert("end", f"[{timestamp}] {prefix} {msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def start_receiver_thread(self):
        self.receiver_thread = ReceiverThread(self)
        self.receiver_thread.start()

    def on_closing(self):
        if self.receiver_thread:
            self.log("🛑 Shutting down receiver thread...", "info")
            self.receiver_thread.stop()
            # give the thread a short moment to exit cleanly
            self.receiver_thread.join(timeout=2)
        self.destroy()

# ============================
# ENTRY POINT
# ============================
if __name__ == "__main__":
    show_splash()
    ensure_default_files()
    silent_mode = '--silent' in sys.argv
    app = SteamDeckApp(silent_mode)
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
