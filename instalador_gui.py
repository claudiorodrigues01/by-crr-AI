import customtkinter as ctk
import subprocess
import threading
import os
import sys
from pathlib import Path
import shutil
from PIL import Image, ImageDraw
import time
import requests

# --- Funções de Instalação ---

def check_python():
    """Verifica se o Python 3.8+ está instalado."""
    try:
        version = sys.version_info
        if version.major < 3 or (version.major == 3 and version.minor < 8):
            update_status("Erro: Python 3.8+ é necessário.")
            return False
        update_status("Python 3.8+ detectado.")
        return True
    except Exception as e:
        update_status(f"Erro ao verificar Python: {e}")
        return False

def create_virtual_env():
    """Cria um ambiente virtual."""
    if not Path("venv").exists():
        update_status("Criando ambiente virtual (venv)...")
        try:
            subprocess.check_call([sys.executable, "-m", "venv", "venv"])
            update_status("Ambiente virtual criado com sucesso.")
            return True
        except subprocess.CalledProcessError as e:
            update_status(f"Erro ao criar venv: {e}")
            return False
    else:
        update_status("Ambiente virtual já existe.")
        return True

def get_pip_path():
    """Obtém o caminho para o pip no ambiente virtual."""
    if sys.platform == "win32":
        return str(Path("venv") / "Scripts" / "pip.exe")
    else:
        return str(Path("venv") / "bin" / "pip")

def install_dependencies():
    """Instala as dependências do requirements.txt."""
    if not create_virtual_env():
        return False
        
    pip_path = get_pip_path()
    requirements_path = "requirements.txt"
    
    if not Path(requirements_path).exists():
        update_status("Erro: requirements.txt não encontrado.")
        return False

    update_status("Instalando dependências... Isso pode levar alguns minutos.")
    try:
        command = [pip_path, "install", "-r", requirements_path, "--upgrade"]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        
        # Leitura em tempo real da saída
        while True:
            output = process.stdout.readline()
            if output == '' and process.poll() is not None:
                break
            if output:
                update_status(output.strip())
        
        stderr = process.communicate()[1]
        if process.returncode != 0:
            update_status(f"Erro ao instalar dependências: {stderr}")
            return False
            
        update_status("Dependências instaladas com sucesso.")
        return True
    except Exception as e:
        update_status(f"Erro inesperado ao instalar dependências: {e}")
        return False

def create_directories():
    """Cria os diretórios necessários para o sistema."""
    update_status("Criando diretórios...")
    try:
        Path("warpclone_config").mkdir(exist_ok=True)
        Path("warpclone_logs").mkdir(exist_ok=True)
        Path("warpclone_memory").mkdir(exist_ok=True)
        Path("assets").mkdir(exist_ok=True)
        update_status("Diretórios criados com sucesso.")
        return True
    except Exception as e:
        update_status(f"Erro ao criar diretórios: {e}")
        return False

def generate_icon():
    """Gera um ícone de hexágono azul e uma imagem PNG para branding."""
    try:
        size = 256
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Coordenadas do hexágono
        r = size // 2 - 16
        cx, cy = size // 2, size // 2
        import math
        points = [
            (cx + r * math.cos(math.radians(60 * i)), cy + r * math.sin(math.radians(60 * i)))
            for i in range(6)
        ]
        # Fundo escuro e contorno neon
        draw.polygon(points, fill=(13, 19, 35, 255))
        draw.line(points + [points[0]], fill=(0, 180, 255, 255), width=6)
        # Salva PNG e ICO
        png_path = Path("assets") / "icon.png"
        ico_path = Path("assets") / "icon.ico"
        img.save(png_path)
        img.save(ico_path, sizes=[(256, 256), (128, 128), (64, 64), (32, 32)])
        update_status("Ícone gerado com sucesso.")
        return True
    except Exception as e:
        update_status(f"Falha ao gerar ícone: {e}")
        return False

def create_shortcut():
    """Cria um atalho na área de trabalho."""
    if sys.platform != "win32":
        update_status("Criação de atalho é suportada apenas no Windows.")
        return True # Não é um erro fatal em outras plataformas

    update_status("Criando atalho na área de trabalho...")
    try:
        import winshell
        from win32com.client import Dispatch

        desktop = winshell.desktop()
        link_filepath = str(Path(desktop) / "By-CRR Soluções em Tecnologia AI.lnk")

        # Use caminhos absolutos para evitar o aviso do Windows sobre 'pythonw.exe' movido/alterado
        python_executable = os.path.abspath(os.path.join("venv", "Scripts", "pythonw.exe"))
        target = os.path.abspath(os.path.join(Path.cwd(), "warpclone_gui.py"))
        workdir = os.path.abspath(str(Path.cwd()))
        icon = os.path.abspath(os.path.join(Path.cwd(), "assets", "icon.ico"))

        shell = Dispatch('WScript.Shell')
        shortcut = shell.CreateShortCut(link_filepath)
        shortcut.Targetpath = python_executable
        shortcut.Arguments = f'"{target}"'
        shortcut.WorkingDirectory = workdir
        shortcut.IconLocation = icon
        shortcut.save()
        
        update_status("Atalho criado com sucesso!")
        return True
    except ImportError:
        update_status("Módulo 'winshell' não encontrado. Pulando criação de atalho.")
        update_status("Para criar o atalho, execute: venv\Scripts\pip.exe install winshell")
        return True # Não é um erro fatal
    except Exception as e:
        update_status(f"Erro ao criar atalho: {e}")
        return False

def run_installation():
    """Executa o processo de instalação completo."""
    if not check_python():
        return

    if not create_directories():
        return

    # Gera ícone de app
    generate_icon()

    if not install_dependencies():
        return

    if not create_shortcut():
        return
    
    # Verifica Ollama server e oferece iniciar se necessário
    verify_ollama_server()
        
    update_status("\nINSTALAÇÃO CONCLUÍDA COM SUCESSO!")
    update_status("Você pode fechar esta janela ou iniciar o programa pelo atalho.")
    start_button.configure(state="disabled")


# --- Interface Gráfica ---

class InstallerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Instalador - By-CRR Soluções em Tecnologia AI")
        self.geometry("700x500")
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Título
        title_label = ctk.CTkLabel(self, text="Instalador By-CRR AI", font=ctk.CTkFont(size=24, weight="bold"))
        title_label.grid(row=0, column=0, padx=20, pady=(20, 10))

        # Frame de Status
        status_frame = ctk.CTkFrame(self)
        status_frame.grid(row=1, column=0, padx=20, pady=10, sticky="nsew")
        status_frame.grid_columnconfigure(0, weight=1)
        status_frame.grid_rowconfigure(0, weight=1)

        self.status_textbox = ctk.CTkTextbox(status_frame, wrap="word", state="disabled", font=ctk.CTkFont(size=12))
        self.status_textbox.grid(row=0, column=0, sticky="nsew")

        # Barra de Progresso
        self.progress_bar = ctk.CTkProgressBar(self, mode='indeterminate')
        self.progress_bar.grid(row=2, column=0, padx=20, pady=10, sticky="ew")
        self.progress_bar.set(0)

        # Botão de Iniciar
        global start_button
        start_button = ctk.CTkButton(self, text="INICIAR INSTALAÇÃO", command=self.start_installation_thread, font=ctk.CTkFont(size=16, weight="bold"))
        start_button.grid(row=3, column=0, padx=20, pady=20, sticky="ew")

    def start_installation_thread(self):
        """Inicia a instalação em uma thread separada para não bloquear a GUI."""
        start_button.configure(state="disabled")
        self.progress_bar.start()
        
        install_thread = threading.Thread(target=run_installation)
        install_thread.daemon = True
        install_thread.start()

    def update_status_gui(self, message):
        """Atualiza a caixa de texto de status na GUI."""
        self.status_textbox.configure(state="normal")
        self.status_textbox.insert("end", message + "\n")
        self.status_textbox.configure(state="disabled")
        self.status_textbox.see("end")
        self.update_idletasks() # Força a atualização da GUI

def update_status(message):
    """Função global para atualizar o status na GUI."""
    print(message) # Log para o console
    if app:
        app.after(0, app.update_status_gui, message)

def check_ollama_cli():
    """Verifica se o comando 'ollama' está disponível."""
    try:
        result = subprocess.run(["ollama", "version"], capture_output=True, text=True)
        return result.returncode == 0
    except Exception:
        return False

def is_ollama_running():
    """Verifica se o Ollama server está respondendo na porta 11434."""
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False

def start_ollama_server():
    """Tenta iniciar o Ollama server em um novo console com retry inteligente."""
    try:
        CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        subprocess.Popen(
            ["ollama", "serve"],
            creationflags=CREATE_NEW_CONSOLE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        update_status("Iniciando ollama serve...")
        
        # Retry com backoff exponencial
        for wait in [1, 2, 3, 4, 5]:
            time.sleep(wait)
            if is_ollama_running():
                update_status("✓ Ollama iniciado com sucesso!")
                return True
        
        update_status("⚠ Ollama não respondeu após 15 segundos")
        return False
    except Exception as e:
        update_status(f"Falha ao iniciar ollama serve: {e}")
        return False

def verify_ollama_server():
    """Fluxo: se Ollama está rodando, informa pronto e garante o modelo; se não, pergunta para iniciar."""
    def ensure_model(model_name="phi4:latest"):
        try:
            r = requests.get("http://localhost:11434/api/tags", timeout=3)
            if r.status_code == 200:
                data = r.json()
                models = [m.get("name") for m in data.get("models", [])]
                if model_name not in models:
                    update_status(f"Modelo '{model_name}' não encontrado. Executando 'ollama pull {model_name}'...")
                    subprocess.run(["ollama", "pull", model_name], check=False)
                    r2 = requests.get("http://localhost:11434/api/tags", timeout=3)
                    if r2.status_code == 200 and model_name in [m.get("name") for m in r2.json().get("models", [])]:
                        update_status(f"Modelo '{model_name}' preparado com sucesso.")
                    else:
                        update_status(f"Não foi possível preparar '{model_name}'. Verifique sua instalação do Ollama e o espaço em disco.")
                else:
                    update_status(f"Modelo '{model_name}' já disponível.")
        except Exception as e:
            update_status(f"Falha ao verificar/preparar modelos: {e}")

    if is_ollama_running():
        update_status("Ollama server detectado em http://localhost:11434 – pronto para funcionar.")
        ensure_model("phi4:latest")
        return
    
    # Não está rodando
    if not check_ollama_cli():
        update_status("Ollama CLI não encontrado. Instale o Ollama Desktop/CLI para usar agentes.")
        update_status("Baixe em: https://ollama.com/download")
        return
    
    # Pergunta ao usuário
    try:
        from tkinter import messagebox
        if messagebox.askyesno("Ollama Server", "O Ollama não está rodando. Deseja iniciar 'ollama serve' agora?"):
            start_ollama_server()
            if is_ollama_running():
                update_status("Ollama iniciado com sucesso – o programa está pronto.")
                ensure_model("phi4:latest")
                return
            else:
                update_status("Ainda não foi possível detectar o Ollama.")
        
        # Se chegou aqui, o Ollama não está rodando ou falhou ao iniciar
        if not messagebox.askyesno("Falha no Modo Online", 
                                   "O sistema não conseguiu conectar ao modo Online (IA).\n\n"
                                   "Sem isso, o sistema funcionará apenas com comandos básicos pré-definidos.\n\n"
                                   "Deseja continuar a instalação apenas com o modo Offline?"):
            update_status("Instalação cancelada pelo usuário devido à falha no modo Online.")
            # Remove atalho se foi criado, para não deixar "lixo"
            try:
                import winshell
                desktop = winshell.desktop()
                link = Path(desktop) / "By-CRR Soluções em Tecnologia AI.lnk"
                if link.exists():
                    link.unlink()
                    update_status("Atalho removido.")
            except Exception:
                pass
            return

        update_status("AVISO: Você optou por continuar em MODO OFFLINE. A inteligência artificial não estará disponível.")
        
    except Exception:
        update_status("Não foi possível exibir diálogo de confirmação para o Ollama.")

if __name__ == "__main__":
    app = InstallerApp()
    
    # Adiciona uma mensagem inicial
    update_status("Bem-vindo ao instalador do By-CRR Soluções em Tecnologia AI.")
    update_status("Clique em 'INICIAR INSTALAÇÃO' para começar.")
    
    app.mainloop()