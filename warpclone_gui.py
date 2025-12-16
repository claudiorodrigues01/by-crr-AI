import customtkinter as ctk
import tkinter as tk
from tkinter import scrolledtext, messagebox
import threading
import requests
import json
import sys
from pathlib import Path
from PIL import Image
from warpclone import WarpClone
import subprocess
import time

class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Janela e tema
        self.title("By-CRR Soluções em Tecnologia AI")
        self.geometry("960x640")
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        self.configure(fg_color="#0b0f19")

        # Ícone da janela (se disponível)
        icon_path = Path("assets/icon.ico")
        try:
            if icon_path.exists():
                self.iconbitmap(default=str(icon_path))
        except Exception:
            pass

        # Layout principal
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Sidebar com branding
        sidebar = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color="#0d1323")
        sidebar.grid(row=0, column=0, rowspan=4, sticky="nsew")
        sidebar.grid_columnconfigure(0, weight=1)
        sidebar.grid_rowconfigure(3, weight=1)

        brand_img = None
        img_path = Path("assets/icon.png")
        if img_path.exists():
            try:
                brand_img = ctk.CTkImage(light_image=Image.open(img_path), dark_image=Image.open(img_path), size=(80, 80))
            except Exception:
                brand_img = None
        if brand_img:
            ctk.CTkLabel(sidebar, image=brand_img, text="").grid(row=0, column=0, padx=20, pady=(30, 10))

        ctk.CTkLabel(sidebar, text="By-CRR AI", font=ctk.CTkFont(size=22, weight="bold"))\
            .grid(row=1, column=0, padx=20, pady=(10, 4))
        ctk.CTkLabel(sidebar, text="Cyberpunk Assistant", font=ctk.CTkFont(size=14))\
            .grid(row=2, column=0, padx=20, pady=(0, 10))

        # Linha neon decorativa
        neon_line = ctk.CTkFrame(sidebar, height=2, fg_color="#00b4ff")
        neon_line.grid(row=3, column=0, padx=20, pady=10, sticky="ew")

        # Gerenciamento de Conversas
        ctk.CTkLabel(sidebar, text="Conversas", font=ctk.CTkFont(size=16, weight="bold"))\
            .grid(row=4, column=0, padx=20, pady=(5, 5), sticky="w")
        self.session_selector = ctk.CTkComboBox(sidebar, values=[""], state="readonly")
        self.session_selector.grid(row=5, column=0, padx=20, pady=(0, 8), sticky="ew")
        self.new_session_btn = ctk.CTkButton(sidebar, text="Nova Conversa", command=self.new_session,
                                             fg_color="#00b4ff", hover_color="#0094d8")
        self.new_session_btn.grid(row=6, column=0, padx=20, pady=(0, 8), sticky="ew")
        self.load_session_btn = ctk.CTkButton(sidebar, text="Carregar Conversa", command=self.load_selected_session)
        self.load_session_btn.grid(row=7, column=0, padx=20, pady=(0, 20), sticky="ew")
        
        # Status de conexão Ollama
        status_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        status_frame.grid(row=8, column=0, padx=20, pady=(10, 10), sticky="ew")
        ctk.CTkLabel(status_frame, text="Status Ollama:", font=ctk.CTkFont(size=12))\
            .pack(anchor="w")
        self.ollama_status_label = ctk.CTkLabel(status_frame, text="Verificando...", 
                                                font=ctk.CTkFont(size=11), text_color="#fbbf24")
        self.ollama_status_label.pack(anchor="w", pady=(2, 0))
        # Modelo em uso
        self.model_label = ctk.CTkLabel(status_frame, text="Modelo: N/D", 
                                        font=ctk.CTkFont(size=11), text_color="#93c5fd")
        self.model_label.pack(anchor="w", pady=(2, 0))

        # Área principal
        main_frame = ctk.CTkFrame(self, fg_color="#0d1323", border_color="#00b4ff", border_width=2)
        main_frame.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_rowconfigure(1, weight=1)

        # Título
        title_label = ctk.CTkLabel(main_frame, text="By-CRR Soluções em Tecnologia AI", font=ctk.CTkFont(size=24, weight="bold"))
        title_label.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

        # Saída de Texto
        self.output_textbox = scrolledtext.ScrolledText(main_frame, wrap=tk.WORD, state="disabled",
                                                        bg="#0b0f19", fg="#e5e7eb", insertbackground="#e5e7eb", font=("Consolas", 12))
        self.output_textbox.grid(row=1, column=0, padx=20, pady=10, sticky="nsew")

        # Entrada de Texto
        self.input_entry = ctk.CTkEntry(self, placeholder_text="Digite sua tarefa aqui...",
                                        font=ctk.CTkFont(size=14), fg_color="#111827")
        self.input_entry.grid(row=2, column=1, padx=20, pady=(0, 10), sticky="ew")
        self.input_entry.bind("<Return>", self.send_task_event)

        # Botão de Enviar
        self.send_button = ctk.CTkButton(self, text="Enviar Tarefa", command=self.send_task,
                                         font=ctk.CTkFont(size=16, weight="bold"),
                                         fg_color="#00b4ff", hover_color="#0094d8")
        self.send_button.grid(row=3, column=1, padx=20, pady=(0, 20), sticky="ew")

        # Instancia o WarpClone e conecta o handler de confirmação
        self.warp = WarpClone()
        self.warp.set_confirmation_handler(self.confirm_command_gui)

        # Prepara lista de sessões
        self._session_display_to_id = {}
        self.refresh_session_list()
        # Se não houver sessão ativa, cria uma
        if not self.warp.session_id:
            self.new_session()
        
        # Atualiza status da conexão Ollama
        self.update_ollama_status()

    def update_ollama_status(self):
        """Atualiza o indicador visual de status do Ollama."""
        try:
            if self.warp.ollama_available and self.warp.llm_enabled:
                self.ollama_status_label.configure(text="\u2713 Conectado", text_color="#10b981")
                self.model_label.configure(text=f"Modelo: {self.warp.model}", text_color="#93c5fd")
            elif self.warp.offline_mode:
                self.ollama_status_label.configure(text="\u26a0 Modo Offline", text_color="#fbbf24")
                self.model_label.configure(text=f"Modelo: {self.warp.model} (offline)", text_color="#fbbf24")
            else:
                self.ollama_status_label.configure(text="\u2717 Desconectado", text_color="#ef4444")
                self.model_label.configure(text="Modelo: N/D", text_color="#ef4444")
        except Exception:
            self.ollama_status_label.configure(text="\u2717 Erro", text_color="#ef4444")
            try:
                self.model_label.configure(text="Modelo: N/D", text_color="#ef4444")
            except Exception:
                pass
    
    def add_to_output(self, message, tag=None):
        """Adiciona uma mensagem à caixa de saída com formatação opcional."""
        self.output_textbox.configure(state="normal")
        self.output_textbox.insert(tk.END, message + "\n", tag)
        self.output_textbox.configure(state="disabled")
        self.output_textbox.see(tk.END)
        self.update_idletasks()

    def clear_output(self):
        self.output_textbox.configure(state="normal")
        self.output_textbox.delete("1.0", tk.END)
        self.output_textbox.configure(state="disabled")
        self.update_idletasks()

    def render_history_to_output(self):
        """Renderiza mensagens da sessão atual no painel de saída."""
        self.clear_output()
        try:
            for msg in self.warp.conversation_history:
                role = msg.get("role")
                content = msg.get("content")
                prefix = "👨‍💻 Usuário: " if role == "user" else "🤖 By-CRR AI: "
                self.add_to_output(prefix + str(content), role)
        except Exception:
            pass

    def refresh_session_list(self):
        items = self.warp.list_sessions()
        values = []
        mapping = {}
        for it in items:
            label = f"{it.get('name','Sessão')}"
            values.append(label)
            mapping[label] = it.get("id")
        if not values:
            values = [""]
        self._session_display_to_id = mapping
        self.session_selector.configure(values=values)
        if values:
            self.session_selector.set(values[0])

    def new_session(self):
        sid = self.warp.start_new_session()
        self.refresh_session_list()
        # Seleciona a nova sessão pelo nome
        try:
            for label, _id in self._session_display_to_id.items():
                if _id == sid:
                    self.session_selector.set(label)
                    break
        except Exception:
            pass
        self.clear_output()
        self.add_to_output("Nova conversa iniciada.")

    def load_selected_session(self):
        label = self.session_selector.get()
        sid = self._session_display_to_id.get(label)
        if not sid:
            messagebox.showwarning("Conversas", "Selecione uma conversa na lista.")
            return
        ok = self.warp.load_session(sid)
        if ok:
            self.render_history_to_output()
        else:
            messagebox.showerror("Conversas", "Falha ao carregar a conversa selecionada.")

    def send_task_event(self, event):
        self.send_task()

    def send_task(self):
        task = self.input_entry.get()
        if not task:
            messagebox.showwarning("Aviso", "O campo de tarefa não pode estar vazio.")
            return
        # Garante uma sessão ativa
        if not self.warp.session_id:
            self.new_session()
        
        self.add_to_output(f"👨‍💻 Usuário: {task}", "user")
        self.input_entry.delete(0, tk.END)
        
        self.send_button.configure(state="disabled", text="Processando...")
        
        thread = threading.Thread(target=self.run_task_thread, args=(task,))
        thread.daemon = True
        thread.start()
        # Watchdog para reativar UI se algo demorar demais
        try:
            def watchdog_fire():
                try:
                    self.task_completed("Tempo limite excedido ao processar a tarefa.")
                except Exception:
                    pass
            self._pending_watchdog = threading.Timer(95.0, watchdog_fire)
            self._pending_watchdog.daemon = True
            self._pending_watchdog.start()
        except Exception:
            self._pending_watchdog = None

    def run_task_thread(self, task):
        """Função que roda na thread para executar a tarefa."""
        try:
            result = self.warp.execute_task(task)
            self.after(0, self.task_completed, result)
        except Exception as e:
            self.after(0, self.task_completed, f"Ocorreu um erro crítico: {e}")

    def task_completed(self, result):
        """Callback para quando a tarefa é concluída."""
        # Cancela watchdog, se estiver ativo
        try:
            if getattr(self, "_pending_watchdog", None):
                self._pending_watchdog.cancel()
                self._pending_watchdog = None
        except Exception:
            pass
        try:
            if isinstance(result, tuple) and len(result) == 2:
                final_answer, last_action_result = result
                message = final_answer
                if last_action_result:
                    message = f"{final_answer}\n\nÚltima ação:\n{last_action_result}"
            else:
                message = str(result)
        except Exception:
            message = str(result)

        self.add_to_output(f"🤖 By-CRR AI: {message}", "assistant")
        
        self.send_button.configure(state="normal", text="Enviar Tarefa")

    def confirm_command_gui(self, command, reason):
        """Mostra um diálogo de confirmação para comandos sensíveis e retorna True/False conforme o usuário."""
        response_box = {"value": None}
        done_event = threading.Event()

        def ask():
            try:
                msg = (
                    "O sistema identificou um comando potencialmente sensível.\n\n"
                    f"Comando:\n{command}\n\nMotivo: {reason}\n\n"
                    "Deseja executar mesmo assim?"
                )
                response_box["value"] = messagebox.askyesno("Confirmação de Comando Sensível", msg)
            except Exception:
                response_box["value"] = False
            finally:
                done_event.set()

        # Garante que o diálogo abre no thread da GUI
        self.after(0, ask)
        done_event.wait()
        return bool(response_box["value"])


def check_ollama_running():
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        return r.status_code == 200
    except Exception:
        return False

if __name__ == "__main__":

    # Respeita modo offline definido em warpclone_config.json
    offline_mode = False
    try:
        cfg_path = Path("warpclone_config.json")
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                offline_mode = bool(cfg.get("offline_mode", False))
    except Exception:
        offline_mode = False

    if not offline_mode and not check_ollama_running():
        if messagebox.askyesno("Ollama Server", "O Ollama não está rodando. Deseja iniciar 'ollama serve' agora?"):
            try:
                CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                subprocess.Popen(
                    ["ollama", "serve"],
                    creationflags=CREATE_NEW_CONSOLE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                # Retry com backoff para garantir inicialização
                for wait in [1, 2, 3, 4, 5]:
                    time.sleep(wait)
                    if check_ollama_running():
                        messagebox.showinfo("Sucesso", "Ollama iniciado com sucesso!")
                        break
            except Exception:
                pass
        
        # Verificação final
        if not check_ollama_running():
            messagebox.showwarning(
                "Ollama não encontrado",
                "Não foi possível conectar ao Ollama.\n\n"
                "A aplicação iniciará em MODO LIMITADO.\n"
                "Para recursos completos de IA, inicie o Ollama manualmente."
            )

    app = App()
    app.mainloop()
