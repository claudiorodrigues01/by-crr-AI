@echo off
chcp 65001 >nul
title By-CRR AI - Iniciando...

echo.
echo ╔════════════════════════════════════════════════════════════╗
echo ║         By-CRR Soluções em Tecnologia AI                  ║
echo ║                    Iniciando Sistema                       ║
echo ╚════════════════════════════════════════════════════════════╝
echo.

:: Verifica se Ollama está rodando
echo [1/3] Verificando Ollama Server...
powershell -Command "try { $response = Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' -TimeoutSec 2 -UseBasicParsing; exit 0 } catch { exit 1 }"

if %ERRORLEVEL% EQU 0 (
    echo ✓ Ollama Server conectado!
) else (
    echo ✗ Ollama não está rodando
    echo.
    echo Tentando iniciar Ollama Server...
    start "" "ollama" serve
    timeout /t 5 /nobreak >nul
    
    :: Verifica novamente
    powershell -Command "try { $response = Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' -TimeoutSec 2 -UseBasicParsing; exit 0 } catch { exit 1 }"
    
    if %ERRORLEVEL% EQU 0 (
        echo ✓ Ollama Server iniciado com sucesso!
    ) else (
        echo ⚠ Ollama não disponível - sistema iniciará em modo limitado
        timeout /t 3 /nobreak >nul
    )
)

echo.
echo [2/3] Verificando Python e dependências...
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ✗ Python não encontrado! Instale Python 3.8+ primeiro.
    pause
    exit /b 1
)
echo ✓ Python instalado

echo.
echo [3/3] Iniciando By-CRR AI...
echo.

:: Prioriza executável se existir
if exist "dist\ByCRR_AI.exe" (
    echo Iniciando executável...
    start "" "dist\ByCRR_AI.exe"
) else if exist "warpclone_gui.py" (
    echo Iniciando via Python...
    python warpclone_gui.py
) else (
    echo ✗ Arquivos do sistema não encontrados!
    echo Certifique-se de estar no diretório correto.
    pause
    exit /b 1
)

echo.
echo ✓ Sistema iniciado!
timeout /t 2 /nobreak >nul
