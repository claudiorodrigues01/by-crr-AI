import customtkinter as ctk
import shutil
import os
from pathlib import Path
import sys
import threading
import subprocess
from tkinter import messagebox

# --- Funções de Desinstalação ---

def remove_directories(remove_data=False):
    """Remove os diretórios da aplicação e, opcionalmente, os dados do usuário."""
    update_status("Removendo diretórios da aplicação...")
    
    # Remove o ambiente virtual
    venv_path = Path("venv")
    if venv_path.exists() and venv_path.is_dir():
        try:
            shutil.rmtree(venv_path)
            update_status("Ambiente virtual 'venv' removido.")
        except Exception as e:
            update_status(f"Erro ao remover 'venv': {e}")

    if remove_data:
        update_status("Removendo diretórios de dados do usuário...")
        for dir_name in ["warpclone_config", "warpclone_logs", "warpclone_memory", "warpclone_knowledge", "assets"]:
            dir_path = Path(dir_name)
            if dir_path.exists() and dir_path.is_dir():
                try:
                    shutil.rmtree(dir_path)
                    update_status(f"Diretório '{dir_name}' removido.")
                except Exception as e:
                    update_status(f"Erro ao remover '{dir_name}': {e}")
    else:
        update_status("Dados do usuário mantidos.")

def remove_shortcut():
    """Remove o atalho da área de trabalho."""
    if sys.platform != "win32":
        update_status("Remoção de atalho é suportada apenas no Windows.")
        return

    update_status("Removendo atalho da área de trabalho...")
    try:
        import winshell
        desktop = winshell.desktop()
        link_filepath = str(Path(desktop) / "By-CRR Soluções em Tecnologia AI.lnk")
        if Path(link_filepath).exists():
            os.remove(link_filepath)
            update_status("Atalho removido com sucesso.")
        else:
            update_status("Atalho não encontrado.")
    except ImportError:
        update_status("Módulo 'winshell' não encontrado. Pulando remoção de atalho.")
    except Exception as e:
        update_status(f"Erro ao remover atalho: {e}")

def remove_remaining_files():
    """Remove os arquivos restantes da aplicação, exceto o desinstalador."""
    update_status("Removendo arquivos da aplicação...")
    current_script = Path(__file__).name
    
    for item in Path.cwd().iterdir():
        # Não remover o próprio desinstalador enquanto ele está rodando
        if item.name == current_script or item.name == "desinstalar.bat":
            continue
        
        try:
            if item.is_dir():
                # Apenas remove diretórios se estiverem vazios (já tratados)
                if not any(item.iterdir()):
                    item.rmdir()
            else:
                item.unlink()
                update_status(f"Arquivo '{item.name}' removido.")
        except Exception as e:
            update_status(f"Não foi possível remover '{item.name}': {e}")


def run_uninstallation(remove_data=False):
    """Executa o processo de desinstalação completo."""
    
    # Confirmação final
    if not messagebox.askyesno("Confirmar Desinstalação", 
                               "Você tem certeza que deseja desinstalar o By-CRR AI? Esta ação não pode ser desfeita."):
        update_status("Desinstalação cancelada pelo usuário.")
        start_button.configure(state="normal")
        return

    start_button.configure(state="disabled")
    
    remove_shortcut()
    remove_directories(remove_data)
    remove_remaining_files()
    
    update_status("\nDESINSTALAÇÃO QUASE COMPLETA!")
    update_status("Resta apenas remover este desinstalador.")
    update_status("Você pode fechar esta janela e deletar o arquivo 'desinstalador_gui.py' e 'desinstalar.bat' manualmente.")
    
    # Tenta se auto-deletar (pode não funcionar em todos os sistemas)
    self_destruct()

def self_destruct():
    """Cria um script .bat para deletar o desinstalador e o próprio .bat."""
    if sys.platform != "win32":
        return

    script_path = Path(__file__)
    bat_script = f"""
@echo off
echo Aguardando o desinstalador fechar...
timeout /t 2 /nobreak > nul
del "{script_path.name}"
del "%~f0"
"""
    bat_path = Path.cwd() / "cleanup.bat"
    with open(bat_path, "w") as f:
        f.write(bat_script)
    
    # Executa o .bat em um novo processo
    subprocess.Popen(f'start /b "" "{bat_path}"', shell=True, close_fds=True)
    
    # Fecha a aplicação
    app.quit()


# --- Interface Gráfica ---

class UninstallerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Desinstalador - By-CRR Soluções em Tecnologia AI")
        self.geometry("700x500")
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Título
        title_label = ctk.CTkLabel(self, text="Desinstalador By-CRR AI", font=ctk.CTkFont(size=24, weight="bold"))
        title_label.grid(row=0, column=0, padx=20, pady=(20, 10))

        # Frame de Status
        status_frame = ctk.CTkFrame(self)
        status_frame.grid(row=1, column=0, padx=20, pady=10, sticky="nsew")
        status_frame.grid_columnconfigure(0, weight=1)
        status_frame.grid_rowconfigure(0, weight=1)

        self.status_textbox = ctk.CTkTextbox(status_frame, wrap="word", state="disabled", font=ctk.CTkFont(size=12))
        self.status_textbox.grid(row=0, column=0, sticky="nsew")

        # Checkbox para remover dados
        self.remove_data_var = ctk.StringVar(value="off")
        remove_data_check = ctk.CTkCheckBox(self, text="Remover todos os dados do usuário (logs, configurações, memória)",
                                            variable=self.remove_data_var, onvalue="on", offvalue="off")
        remove_data_check.grid(row=2, column=0, padx=20, pady=10, sticky="w")

        # Botão de Iniciar
        global start_button
        start_button = ctk.CTkButton(self, text="DESINSTALAR", command=self.start_uninstallation_thread, 
                                     font=ctk.CTkFont(size=16, weight="bold"), fg_color="#D32F2F", hover_color="#B71C1C")
        start_button.grid(row=3, column=0, padx=20, pady=20, sticky="ew")

    def start_uninstallation_thread(self):
        """Inicia a desinstalação em uma thread separada."""
        remove_data = self.remove_data_var.get() == "on"
        
        uninstall_thread = threading.Thread(target=run_uninstallation, args=(remove_data,))
        uninstall_thread.daemon = True
        uninstall_thread.start()

    def update_status_gui(self, message):
        """Atualiza a caixa de texto de status na GUI."""
        self.status_textbox.configure(state="normal")
        self.status_textbox.insert("end", message + "\n")
        self.status_textbox.configure(state="disabled")
        self.status_textbox.see("end")
        self.update_idletasks()

def update_status(message):
    """Função global para atualizar o status na GUI."""
    print(message)
    if app:
        app.after(0, app.update_status_gui, message)

if __name__ == "__main__":
    app = UninstallerApp()
    update_status("Bem-vindo ao desinstalador do By-CRR Soluções em Tecnologia AI.")
    update_status("AVISO: Este processo removerá os arquivos da aplicação.")
    update_status("Selecione a opção se desejar remover também seus dados e configurações.")
    app.mainloop()