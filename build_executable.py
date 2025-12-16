#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Build script melhorado para By-CRR AI
Gera executáveis: ByCRR_AI.exe, ByCRR_Installer.exe, ByCRR_Uninstaller.exe
"""

import subprocess
import sys
import os
from pathlib import Path
import shutil

def banner(text):
    """Exibe banner formatado."""
    print("\n" + "="*60)
    print(f"  {text}")
    print("="*60 + "\n")

def check_dependencies():
    """Verifica se PyInstaller está instalado."""
    try:
        import PyInstaller
        print(f"✓ PyInstaller {PyInstaller.__version__} encontrado")
        return True
    except ImportError:
        print("✗ PyInstaller não encontrado")
        print("\nInstalando PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        return True

def clean_build_dirs():
    """Limpa diretórios de build anteriores."""
    print("Limpando builds anteriores...")
    dirs_to_clean = ["build", "dist"]
    for d in dirs_to_clean:
        if Path(d).exists():
            shutil.rmtree(d)
            print(f"  ✓ Removido: {d}/")
    
    # Remove .spec antigos
    for spec in Path(".").glob("*.spec"):
        spec.unlink()
        print(f"  ✓ Removido: {spec.name}")
    
    print()

def build_main_app():
    """Constrói o executável principal (ByCRR_AI.exe)."""
    banner("BUILD: ByCRR_AI.exe (Aplicação Principal)")
    
    icon = "assets/icon.ico" if Path("assets/icon.ico").exists() else None
    
    cmd = [
        "pyinstaller",
        "--name=ByCRR_AI",
        "--onefile",
        "--windowed",
        "--add-data=assets;assets",
        "--add-data=warpclone_config;warpclone_config",
        "--add-data=warpclone_knowledge;warpclone_knowledge",
        "--hidden-import=PIL._tkinter_finder",
        "--collect-all=customtkinter",
    ]
    
    if icon:
        cmd.append(f"--icon={icon}")
    
    cmd.append("warpclone_gui.py")
    
    print("Comando:", " ".join(cmd))
    result = subprocess.run(cmd)
    
    if result.returncode == 0:
        print("\n✓ ByCRR_AI.exe criado com sucesso!")
        return True
    else:
        print("\n✗ Erro ao criar ByCRR_AI.exe")
        return False

def build_installer():
    """Constrói o instalador (ByCRR_Installer.exe)."""
    banner("BUILD: ByCRR_Installer.exe (Instalador)")
    
    icon = "assets/icon.ico" if Path("assets/icon.ico").exists() else None
    
    cmd = [
        "pyinstaller",
        "--name=ByCRR_Installer",
        "--onefile",
        "--windowed",
        "--add-data=assets;assets",
        "--collect-all=customtkinter",
    ]
    
    if icon:
        cmd.append(f"--icon={icon}")
    
    cmd.append("instalador_gui.py")
    
    print("Comando:", " ".join(cmd))
    result = subprocess.run(cmd)
    
    if result.returncode == 0:
        print("\n✓ ByCRR_Installer.exe criado com sucesso!")
        return True
    else:
        print("\n✗ Erro ao criar ByCRR_Installer.exe")
        return False

def build_uninstaller():
    """Constrói o desinstalador (ByCRR_Uninstaller.exe)."""
    banner("BUILD: ByCRR_Uninstaller.exe (Desinstalador)")
    
    icon = "assets/icon.ico" if Path("assets/icon.ico").exists() else None
    
    cmd = [
        "pyinstaller",
        "--name=ByCRR_Uninstaller",
        "--onefile",
        "--windowed",
        "--add-data=assets;assets",
        "--collect-all=customtkinter",
    ]
    
    if icon:
        cmd.append(f"--icon={icon}")
    
    cmd.append("desinstalador_gui.py")
    
    print("Comando:", " ".join(cmd))
    result = subprocess.run(cmd)
    
    if result.returncode == 0:
        print("\n✓ ByCRR_Uninstaller.exe criado com sucesso!")
        return True
    else:
        print("\n✗ Erro ao criar ByCRR_Uninstaller.exe")
        return False

def verify_builds():
    """Verifica se todos os executáveis foram criados."""
    banner("VERIFICAÇÃO DOS BUILDS")
    
    expected_files = [
        "dist/ByCRR_AI.exe",
        "dist/ByCRR_Installer.exe",
        "dist/ByCRR_Uninstaller.exe"
    ]
    
    all_ok = True
    for file in expected_files:
        if Path(file).exists():
            size = Path(file).stat().st_size / (1024 * 1024)
            print(f"  ✓ {file} ({size:.2f} MB)")
        else:
            print(f"  ✗ {file} NÃO ENCONTRADO")
            all_ok = False
    
    return all_ok

def main():
    """Função principal do build."""
    banner("BY-CRR AI - BUILD SYSTEM")
    
    print("Diretório de trabalho:", os.getcwd())
    print()
    
    # 1. Verificar dependências
    if not check_dependencies():
        print("\n✗ Falha ao instalar dependências")
        sys.exit(1)
    
    # 2. Limpar builds anteriores
    clean_build_dirs()
    
    # 3. Build da aplicação principal
    if not build_main_app():
        print("\n✗ Build falhou na aplicação principal")
        sys.exit(1)
    
    # 4. Build do instalador
    if not build_installer():
        print("\n✗ Build falhou no instalador")
        sys.exit(1)
    
    # 5. Build do desinstalador
    if not build_uninstaller():
        print("\n✗ Build falhou no desinstalador")
        sys.exit(1)
    
    # 6. Verificação final
    if verify_builds():
        banner("✓ BUILD COMPLETO COM SUCESSO!")
        print("Os executáveis estão em: dist/")
        print("\nPróximos passos:")
        print("  1. Execute dist/ByCRR_Installer.exe para instalar")
        print("  2. Ou execute dist/ByCRR_AI.exe diretamente")
    else:
        banner("✗ BUILD INCOMPLETO - VERIFIQUE OS ERROS")
        sys.exit(1)

if __name__ == "__main__":
    main()