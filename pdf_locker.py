"""
Python PDF Locker Mixin for Red Circuit / Visual Circuit Builder Pro.
Encrypts PDF files with AES-256 protection using PyMuPDF (fitz).
Renders directly inside the main workspace screen (Single Main Screen Panel).
"""
import os
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk

class PDFLockerMixin:

    def init_pdf_locker(self):
        self._pdf_locker_panel = None

    def open_pdf_locker(self):
        """Displays the Python PDF Locker panel directly inside the main workspace screen."""
        if hasattr(self, "_pdf_locker_panel") and self._pdf_locker_panel is not None and self._pdf_locker_panel.winfo_exists():
            self._pdf_locker_panel.lift()
            return

        # Dynamically import fitz at runtime so newly installed packages work immediately
        try:
            import fitz
        except ImportError:
            fitz = None

        if fitz is None:
            messagebox.showerror(
                "PyMuPDF Missing",
                "The 'PyMuPDF' library (fitz) is not installed.\nPlease click 'Scan & Auto-Install Missing Packages' in More Tools to auto-install it."
            )
            return

        # Embedded frame attached directly to main canvas container (Single Main Screen)
        panel = ctk.CTkFrame(
            self.canvas_container,
            fg_color="#181818",
            corner_radius=12,
            border_width=2,
            border_color="#e67e22"
        )
        panel.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.58, relheight=0.72)
        panel.lift()
        self._pdf_locker_panel = panel

        # Header Bar
        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(12, 6))

        ctk.CTkLabel(
            header,
            text="🔒 Python PDF Locker (AES-256)",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#e67e22"
        ).pack(side="left")

        def close_panel():
            panel.destroy()
            self._pdf_locker_panel = None

        ctk.CTkButton(
            header,
            text="✖",
            width=28,
            height=28,
            corner_radius=14,
            fg_color="#c0392b",
            hover_color="#e74c3c",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=close_panel
        ).pack(side="right")

        # Main Body Frame
        body = ctk.CTkFrame(panel, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=5)

        # 1. File Selection Section
        selected_pdf_path = tk.StringVar(value="")

        file_row = ctk.CTkFrame(body, fg_color="transparent")
        file_row.pack(fill="x", pady=(5, 10))

        path_entry = ctk.CTkEntry(
            file_row,
            placeholder_text="No PDF file selected...",
            textvariable=selected_pdf_path,
            font=ctk.CTkFont(size=11),
            height=32
        )
        path_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        def browse_pdf():
            p = filedialog.askopenfilename(
                title="Select PDF to Lock",
                filetypes=[("PDF files", "*.pdf")]
            )
            if p:
                selected_pdf_path.set(p)
                status_label.configure(text="PDF file loaded. Enter passwords to encrypt.", text_color="#f1c40f")

        ctk.CTkButton(
            file_row,
            text="📁 Select PDF",
            width=120,
            height=32,
            fg_color="#1f6aa5",
            hover_color="#144870",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=browse_pdf
        ).pack(side="right")

        # 2. Password Entry Box
        pwd_frame = ctk.CTkFrame(body, fg_color="#111111", corner_radius=8)
        pwd_frame.pack(fill="x", pady=6, padx=2, ipadx=10, ipady=8)

        ctk.CTkLabel(
            pwd_frame,
            text="Set Encryption Passwords",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#3498db"
        ).pack(anchor="w", padx=10, pady=(4, 8))

        # Enter Password
        p1_row = ctk.CTkFrame(pwd_frame, fg_color="transparent")
        p1_row.pack(fill="x", padx=10, pady=3)
        ctk.CTkLabel(p1_row, text="Enter Password:", width=130, anchor="w", font=ctk.CTkFont(size=11)).pack(side="left")
        pass_entry = ctk.CTkEntry(p1_row, show="*", placeholder_text="Password", height=30)
        pass_entry.pack(side="left", fill="x", expand=True)

        # Confirm Password
        p2_row = ctk.CTkFrame(pwd_frame, fg_color="transparent")
        p2_row.pack(fill="x", padx=10, pady=3)
        ctk.CTkLabel(p2_row, text="Confirm Password:", width=130, anchor="w", font=ctk.CTkFont(size=11)).pack(side="left")
        confirm_entry = ctk.CTkEntry(p2_row, show="*", placeholder_text="Confirm password", height=30)
        confirm_entry.pack(side="left", fill="x", expand=True)

        # Status Label
        status_label = ctk.CTkLabel(
            body,
            text="Ready. Select a PDF file and set a password.",
            font=ctk.CTkFont(size=11),
            text_color="#a0a0a0",
            anchor="w"
        )
        status_label.pack(fill="x", pady=(8, 4))

        # Text Console Log Box
        info_box = ctk.CTkTextbox(
            body,
            height=85,
            fg_color="#0a0a0a",
            text_color="#2ecc71",
            font=("Consolas", 10)
        )
        info_box.pack(fill="x", pady=4)
        info_box.insert("1.0", "Encryption Algorithm: AES-256\nPermissions: 0 (Strict Restricted Access)\nDeflate & Garbage Cleaned: Enabled\n")
        info_box.configure(state="disabled")

        # Encryption Logic
        def execute_lock_pdf():
            pdf_path = selected_pdf_path.get().strip()
            if not pdf_path or not os.path.isfile(pdf_path):
                status_label.configure(text="⚠️ Please select a valid PDF file first.", text_color="#e74c3c")
                messagebox.showwarning("No File Selected", "Please select a valid PDF file to lock.")
                return

            pwd = pass_entry.get()
            pwd_confirm = confirm_entry.get()

            if not pwd:
                status_label.configure(text="⚠️ Password cannot be empty.", text_color="#e74c3c")
                messagebox.showwarning("Invalid Password", "Password cannot be empty.")
                return

            if pwd != pwd_confirm:
                status_label.configure(text="⚠️ Passwords do not match.", text_color="#e74c3c")
                messagebox.showerror(
                    "Password Mismatch",
                    "The passwords do not match.\n\nPlease try again."
                )
                return

            try:
                # Open original PDF
                doc = fitz.open(pdf_path)

                folder = os.path.dirname(pdf_path)
                filename = os.path.splitext(os.path.basename(pdf_path))[0]
                output_path = os.path.join(folder, filename + "_locked.pdf")

                # Encrypt PDF
                doc.save(
                    output_path,
                    encryption=fitz.PDF_ENCRYPT_AES_256,
                    owner_pw=pwd,
                    user_pw=pwd,
                    permissions=0,
                    garbage=4,
                    deflate=True
                )
                doc.close()

                # Verify
                locked = fitz.open(output_path)
                is_locked = locked.needs_pass
                locked.close()

                if is_locked:
                    status_label.configure(text="✅ PDF locked successfully!", text_color="#2ecc71")
                    info_box.configure(state="normal")
                    info_box.delete("1.0", tk.END)
                    info_box.insert(
                        tk.END,
                        "======================================\n"
                        "PDF LOCKED SUCCESSFULLY\n"
                        "======================================\n"
                        "Encryption Standard: AES-256\n"
                        f"Saved Location:\n{output_path}\n"
                    )
                    info_box.configure(state="disabled")

                    if hasattr(self, "toast"):
                        self.toast("🔒 PDF locked successfully!")

                    messagebox.showinfo(
                        "PDF Locked",
                        "PDF locked successfully!\n\n"
                        "Password protection: AES-256\n\n"
                        f"Saved as:\n{output_path}"
                    )
                else:
                    status_label.configure(text="❌ Error: The PDF could not be locked.", text_color="#e74c3c")
                    messagebox.showerror("Error", "The PDF could not be locked.")
            except Exception as e:
                status_label.configure(text=f"❌ Error: {e}", text_color="#e74c3c")
                messagebox.showerror("Encryption Error", f"An error occurred while encrypting:\n{e}")

        # Action Buttons Row
        action_row = ctk.CTkFrame(panel, fg_color="transparent")
        action_row.pack(pady=(4, 14))

        ctk.CTkButton(
            action_row,
            text="🔒 Lock PDF Now",
            width=170,
            height=34,
            fg_color="#d35400",
            hover_color="#a04000",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=execute_lock_pdf
        ).pack(side="left", padx=6)

        ctk.CTkButton(
            action_row,
            text="Close",
            width=90,
            height=34,
            fg_color="#555555",
            hover_color="#777777",
            command=close_panel
        ).pack(side="left", padx=6)
