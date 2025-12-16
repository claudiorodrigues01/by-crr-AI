import requests
import json
import os
import subprocess
from pathlib import Path
import shutil
import re
import hashlib
import zipfile
import base64
try:
    import psutil
except ImportError:
    psutil = None
from urllib.parse import quote_plus, urlparse
import time

class WarpClone:
    def __init__(self, model=None, ollama_url=None, confirmation_handler=None):
        cfg = self._load_config()
        self.model = self._canonical_model_name(model or cfg.get("llm_model", "phi4"))
        self.ollama_url = ollama_url or cfg.get("ollama_url", "http://localhost:11434/api/chat")
        self.conversation_history = []
        self.memory = self.load_memory()
        self.log_dir = Path("warpclone_logs")
        self.log_dir.mkdir(exist_ok=True)
        self.command_history_file = self.log_dir / "command_history.json"
        # Diretório de sessões de chat persistentes
        self.chat_sessions_dir = self.log_dir / "chat_sessions"
        self.chat_sessions_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = None
        self.session_name = None
        self.learning_patterns_file = Path("warpclone_memory") / "learning_patterns.json"
        self.learning_patterns = self.load_learning_patterns()
        self.confirmation_handler = confirmation_handler
        self.confirm_sensitive_commands = bool(cfg.get("confirm_sensitive_commands", True))
        self.command_timeout = int(cfg.get("command_timeout", 30))
        self.use_powershell = bool(cfg.get("use_powershell", False))
        self.knowledge_dir = Path("warpclone_knowledge")

        # Biblioteca de comandos (carregada de JSON)
        self.command_library = self._load_command_library()

        # Flags de LLM e modo offline
        self.offline_mode = bool(cfg.get("offline_mode", False))
        self.ollama_autostart = bool(cfg.get("ollama_autostart", True))
        self.llm_enabled = not self.offline_mode
        self.ollama_available = False
        self._ollama_last_check = 0
        self._ollama_check_interval = int(cfg.get("ollama_check_interval_sec", 30))

        # Tenta garantir servidor e modelo com retry robusto
        try:
            if not self.offline_mode:
                # Primeira verificação
                self.ollama_available = self._ollama_health_check(force=True)
                
                # Se não estiver disponível, tenta iniciar
                if not self.ollama_available and self.ollama_autostart:
                    if self._ollama_cli_available():
                        # _start_ollama_server já faz retry interno
                        started = self._start_ollama_server()
                        if started:
                            self.ollama_available = True
                            self.llm_enabled = True
                        else:
                            self.ollama_available = False
                            self.llm_enabled = False
                    else:
                        self.ollama_available = False
                        self.llm_enabled = False
                
                # Garante modelo disponível se servidor está rodando
                if self.ollama_available:
                    self._ensure_model_available(self.model)
                    self.llm_enabled = True
        except Exception:
            # Não derruba o app por falhas de inicialização do LLM
            self.llm_enabled = False
            self.ollama_available = False

    def _load_config(self):
        cfg_path = Path("warpclone_config.json")
        if cfg_path.exists():
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _load_command_library(self) -> dict:
        """Carrega biblioteca de comandos estruturados de 'warpclone_config/command_library.json'."""
        try:
            lib_path = Path("warpclone_config") / "command_library.json"
            if lib_path.exists():
                with open(lib_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data if isinstance(data, dict) else {"commands": []}
        except Exception:
            pass
        return {"commands": []}

    def _match_command_library(self, text_lower: str):
        """Tenta encontrar um comando na biblioteca pelos aliases, retornando o item."""
        try:
            items = (self.command_library or {}).get("commands", [])
            for item in items:
                aliases = [a.lower() for a in item.get("aliases", [])]
                if any(a in text_lower for a in aliases):
                    return item
        except Exception:
            return None
        return None

    def set_confirmation_handler(self, handler):
        self.confirmation_handler = handler

    def load_memory(self):
        memory_path = Path("warpclone_memory") / "memory.json"
        if memory_path.exists():
            try:
                with open(memory_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {"short_term": [], "long_term": {}}
        return {"short_term": [], "long_term": {}}

    def save_memory(self):
        memory_path = Path("warpclone_memory") / "memory.json"
        memory_path.parent.mkdir(exist_ok=True)
        with open(memory_path, "w", encoding="utf-8") as f:
            json.dump(self.memory, f, indent=4, ensure_ascii=False)
        # Persiste padrões de aprendizado juntamente
        with open(self.learning_patterns_file, "w", encoding="utf-8") as f:
            json.dump(self.learning_patterns, f, indent=4, ensure_ascii=False)

    def load_learning_patterns(self):
        try:
            if self.learning_patterns_file.exists():
                with open(self.learning_patterns_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {"usage_count": 0, "actions": {}, "last_success": None}

    # --- Persistência de sessões de chat ---
    def start_new_session(self, name: str | None = None):
        """Inicia uma nova sessão de chat e persiste um arquivo vazio."""
        try:
            ts = time.strftime("%Y%m%d-%H%M%S")
            self.session_id = f"session-{ts}"
            # Nome amigável (pode ser fornecido)
            self.session_name = name or time.strftime("Sessão %d/%m %H:%M")
            self.conversation_history = []
            meta = {
                "id": self.session_id,
                "name": self.session_name,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "messages": []
            }
            fp = self.chat_sessions_dir / f"{self.session_id}.json"
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)
            return self.session_id
        except Exception:
            # Não quebra a execução se falhar; apenas não persiste
            return None

    def save_session(self):
        """Salva o histórico atual da sessão em disco (se existir session_id)."""
        if not self.session_id:
            return False
        try:
            fp = self.chat_sessions_dir / f"{self.session_id}.json"
            if fp.exists():
                try:
                    data = json.loads(fp.read_text(encoding="utf-8"))
                except Exception:
                    data = {"id": self.session_id, "name": self.session_name or "Sessão", "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "messages": []}
            else:
                data = {"id": self.session_id, "name": self.session_name or "Sessão", "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "messages": []}
            data["messages"] = list(self.conversation_history)
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception:
            return False

    def load_session(self, session_id: str):
        """Carrega uma sessão pelo ID e popula o histórico."""
        try:
            fp = self.chat_sessions_dir / f"{session_id}.json"
            if not fp.exists():
                return False
            data = json.loads(fp.read_text(encoding="utf-8"))
            self.session_id = data.get("id") or session_id
            self.session_name = data.get("name") or self.session_id
            msgs = data.get("messages") or []
            # Garante formato mínimo {role, content}
            self.conversation_history = [
                {"role": m.get("role", "assistant"), "content": m.get("content", "")}
                for m in msgs if isinstance(m, dict)
            ]
            return True
        except Exception:
            return False

    def list_sessions(self):
        """Retorna lista de sessões disponíveis com metadados básicos."""
        try:
            items = []
            for fp in sorted(self.chat_sessions_dir.glob("session-*.json")):
                try:
                    data = json.loads(fp.read_text(encoding="utf-8"))
                    items.append({
                        "id": data.get("id") or fp.stem,
                        "name": data.get("name") or fp.stem,
                        "created_at": data.get("created_at") or ""
                    })
                except Exception:
                    items.append({"id": fp.stem, "name": fp.stem, "created_at": ""})
            # Ordena por created_at desc quando possível
            try:
                items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            except Exception:
                pass
            return items
        except Exception:
            return []

    def log_command(self, command, result):
        log_entry = {"command": command, "result": result}
        
        logs = []
        if self.command_history_file.exists():
            try:
                with open(self.command_history_file, "r", encoding="utf-8") as f:
                    logs = json.load(f)
            except (json.JSONDecodeError, IOError):
                logs = []
        
        logs.append(log_entry)
        
        with open(self.command_history_file, "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=4, ensure_ascii=False)

    def call_ollama(self, task):
        system_prompt = """
        Você é um assistente de IA autônomo chamado By-CRR AI. Sua função é analisar as solicitações do usuário e, de forma autônoma, decidir e executar as ações necessárias para completar a tarefa. Você tem acesso a um conjunto de ferramentas.

        O processo funciona em um loop:
        1. O usuário fornece uma tarefa.
        2. Você analisa a tarefa, pensa no que precisa fazer e escolhe uma ação.
        3. Você retorna a ação em um formato JSON estrito.
        4. O sistema executa a ação e retorna o resultado para você.
        5. Você analisa o resultado e decide o próximo passo, que pode ser outra ação ou uma resposta final ao usuário.

        As ferramentas disponíveis são (com exemplos de uso):
        - `execute_command`: Executa um comando do sistema. (Ex: `{ "action": "execute_command", "parameters": { "command": "dir" } }`)
        - `read_file`: Lê um arquivo. (Ex: `{ "action": "read_file", "parameters": { "path": "arquivo.txt" } }`)
        - `write_file`: Cria/sobrescreve um arquivo. (Ex: `{ "action": "write_file", "parameters": { "path": "novo.txt", "content": "Olá" } }`)
        - `create_file`: Cria arquivo (igual a `write_file`). (Ex: `{ "action": "create_file", "parameters": { "path": "novo.txt", "content": "texto" } }`)
        - `append_file`: Anexa conteúdo ao fim do arquivo. (Ex: `{ "action": "append_file", "parameters": { "path": "log.txt", "content": "linha" } }`)
        - `delete_file`: Remove arquivo (pode pedir confirmação). (Ex: `{ "action": "delete_file", "parameters": { "path": "c:\\temp\\log.txt" } }`)
        - `list_dir`: Lista itens de diretório (recursivo opcional). (Ex: `{ "action": "list_dir", "parameters": { "path": ".", "recursive": false } }`)
        - `create_dir`: Cria diretório (com pais). (Ex: `{ "action": "create_dir", "parameters": { "path": "c:\\temp\\novo" } }`)
        - `delete_dir`: Remove diretório (recursivo por padrão, pode pedir confirmação). (Ex: `{ "action": "delete_dir", "parameters": { "path": "c:\\temp\\antigo", "recursive": true } }`)
        - `copy_file`: Copia arquivo. (Ex: `{ "action": "copy_file", "parameters": { "src": "a.txt", "dst": "b.txt" } }`)
        - `move_file`: Move arquivo. (Ex: `{ "action": "move_file", "parameters": { "src": "a.txt", "dst": "pasta\\a.txt" } }`)
        - `rename_file`: Renomeia arquivo. (Ex: `{ "action": "rename_file", "parameters": { "path": "a.txt", "new_path": "b.txt" } }`)
        - `file_hash`: Calcula hash de arquivo (sha256 padrão). (Ex: `{ "action": "file_hash", "parameters": { "path": "a.txt", "algorithm": "sha256" } }`)
        - `zip_create`: Cria ZIP de arquivo/pasta. (Ex: `{ "action": "zip_create", "parameters": { "source": "pasta", "zip_path": "backup.zip" } }`)
        - `zip_extract`: Extrai ZIP para destino. (Ex: `{ "action": "zip_extract", "parameters": { "zip_path": "backup.zip", "dest": "restaurado" } }`)
        - `download_file`: Baixa arquivo de URL. (Ex: `{ "action": "download_file", "parameters": { "url": "https://.../file.zip", "dest": "caminho\\file.zip" } }`)
        - `search_files`: Pesquisa arquivos por padrão. (Ex: `{ "action": "search_files", "parameters": { "pattern": "*.py" } }`)
        - `search_content`: Busca termo em arquivos. (Ex: `{ "action": "search_content", "parameters": { "term": "def main", "extension": ".py" } }`)
        - `search_regex`: Busca por regex em arquivos. (Ex: `{ "action": "search_regex", "parameters": { "pattern": "TODO", "extension": ".py" } }`)
        - `list_processes`: Lista processos (ordenados por CPU). (Ex: `{ "action": "list_processes", "parameters": { "top_n": 20 } }`)
        - `kill_process`: Encerra processo por PID ou nome (pode pedir confirmação). (Ex: `{ "action": "kill_process", "parameters": { "pid": 1234 } }`)
        - `list_services` (Windows): Lista serviços com filtro opcional. (Ex: `{ "action": "list_services", "parameters": { "filter": "DiagTrack" } }`)
        - `start_service` (Windows): Inicia serviço. (Ex: `{ "action": "start_service", "parameters": { "name": "Spooler" } }`)
        - `stop_service` (Windows): Para serviço (pode pedir confirmação). (Ex: `{ "action": "stop_service", "parameters": { "name": "DiagTrack" } }`)
        - `list_scheduled_tasks` (Windows): Lista tarefas agendadas. (Ex: `{ "action": "list_scheduled_tasks", "parameters": { } }`)
        - `list_network_connections`: Lista conexões de rede. (Ex: `{ "action": "list_network_connections", "parameters": { } }`)
        - `open_ports`: Mostra portas em escuta. (Ex: `{ "action": "open_ports", "parameters": { } }`)
        - `firewall_state` (Windows): Exibe estado do firewall. (Ex: `{ "action": "firewall_state", "parameters": { } }`)
        - `ping_host`: Testa latência para host. (Ex: `{ "action": "ping_host", "parameters": { "host": "8.8.8.8", "count": 4 } }`)
        - `traceroute_host`: Rota até host. (Ex: `{ "action": "traceroute_host", "parameters": { "host": "8.8.8.8" } }`)
        - `get_env`: Lê variável de ambiente. (Ex: `{ "action": "get_env", "parameters": { "name": "Path" } }`)
        - `set_env`: Define variável de ambiente (escopo do processo). (Ex: `{ "action": "set_env", "parameters": { "name": "MY_VAR", "value": "123" } }`)
        - `read_registry` (Windows): Lê chave do registro. (Ex: `{ "action": "read_registry", "parameters": { "path": "HKLM:\\SOFTWARE\\Microsoft" } }`)
        - `write_registry` (Windows): Escreve chave/valor no registro (pode pedir confirmação). (Ex: `{ "action": "write_registry", "parameters": { "path": "HKCU:\\Software\\MyApp", "name": "Enabled", "value": "1", "type": "String" } }`)
        - `analyze_system`: Auditoria completa do sistema e telemetria no Windows. (Ex: `{ "action": "analyze_system", "parameters": { } }`)
        - `web_search`: Busca na web. (Ex: `{ "action": "web_search", "parameters": { "query": "Python decorators" } }`)
        - `fetch_url`: Busca conteúdo de URL. (Ex: `{ "action": "fetch_url", "parameters": { "url": "https://example.com" } }`)
        - `knowledge_search`: Busca base local. (Ex: `{ "action": "knowledge_search", "parameters": { "query": "comandos Windows", "top_k": 5 } }`)
        - `answer`: Resposta final ao usuário. (Ex: `{ "action": "answer", "parameters": { "answer": "Concluído." } }`)

        Seu pensamento e a ação escolhida DEVEM ser retornados em um único bloco JSON. Não inclua nenhum texto fora do JSON.

        Exemplo de resposta JSON:
        {
            "thought": "O usuário pediu para listar os arquivos. Vou usar a ferramenta `execute_command` com o comando 'dir' (ou 'ls' em Linux).",
            "action": "execute_command",
            "parameters": {
                "command": "dir"
            }
        }
        """
        
        self.conversation_history.append({"role": "user", "content": task})
        
        full_context = [{"role": "system", "content": system_prompt}]
        
        for msg in self.conversation_history[-5:]:
            full_context.append(msg)

        try:
            # Verificação antecipada: se a consulta corresponde à biblioteca de comandos,
            # utilize o planejador offline para acionar o plano específico.
            try:
                matched = self._match_command_library((task or "").lower())
            except Exception:
                matched = None
            if matched:
                offline_decision = self._offline_decide_action(task)
                self.conversation_history.append({"role": "assistant", "content": offline_decision})
                self.save_session()
                return offline_decision
            # Saúde do LLM: evita tentativas repetidas quando indisponível
            if self.offline_mode or not self._ollama_health_check():
                offline_decision = self._offline_decide_action(task)
                self.conversation_history.append({"role": "assistant", "content": offline_decision})
                self.save_session()
                return offline_decision

            response = requests.post(
                self.ollama_url,
                json={"model": self.model, "messages": full_context, "format": "json", "stream": False},
                timeout=30
            )
            response.raise_for_status()
            
            response_json = response.json()
            # Ollama /api/chat retorna { message: { content } }
            assistant_message = (
                response_json.get('message', {}).get('content')
                or response_json.get('response')  # fallback para /generate-style
            )
            
            # Salva histórico
            self.conversation_history.append({"role": "assistant", "content": assistant_message})
            # Persiste sessão (se ativa)
            self.save_session()
            # Se o modelo não retornou conteúdo, responde de forma amigável
            if not assistant_message:
                return json.dumps({
                    "thought": "O modelo não retornou conteúdo.",
                    "action": "answer",
                    "parameters": {"answer": "Não recebi resposta do modelo. Tente novamente em alguns segundos."}
                })

            # Se o conteúdo não for um JSON válido, tenta extrair o primeiro objeto
            if self._safe_json_loads(assistant_message) is None:
                start = assistant_message.find('{')
                end = assistant_message.rfind('}')
                if start != -1 and end != -1 and end > start:
                    candidate = assistant_message[start:end+1]
                    if self._safe_json_loads(candidate):
                        return candidate
            return assistant_message
            
        except requests.exceptions.RequestException:
            # Marca LLM como indisponível e retorna decisão offline
            self.llm_enabled = False
            self.ollama_available = False
            self._ollama_last_check = time.time()
            offline_decision = self._offline_decide_action(task)
            self.conversation_history.append({"role": "assistant", "content": offline_decision})
            self.save_session()
            return offline_decision
        except json.JSONDecodeError:
            return json.dumps({"thought": "A resposta do Ollama não foi um JSON válido.", "action": "answer", "parameters": {"answer": "Recebi uma resposta inesperada do modelo de linguagem. Tente novamente."}})

    def _safe_json_loads(self, text: str):
        """Tenta fazer json.loads(text). Se falhar, tenta extrair o primeiro bloco JSON.
        Retorna dict ou None.
        """
        if not text:
            return None
            
        # Remove markdown code blocks if present
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Tenta encontrar o primeiro objeto JSON entre chaves
            start = text.find('{')
            end = text.rfind('}')
            if start != -1 and end != -1 and end > start:
                candidate = text[start:end+1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return None
            return None

    def _canonical_model_name(self, name: str) -> str:
        try:
            if not name:
                return "phi4:latest"
            return name if ":" in name else f"{name}:latest"
        except Exception:
            return "phi4:latest"

    def execute_action(self, action_json):
        try:
            # Permite receber dict diretamente ou texto JSON/string
            if isinstance(action_json, dict):
                action_data = action_json
            else:
                action_data = self._safe_json_loads(action_json)
            if not isinstance(action_data, dict):
                # Se não conseguimos interpretar como JSON, trate como resposta final ao usuário
                return f"FINAL_ANSWER:{str(action_json).strip()}"
            
            action = action_data.get("action")
            parameters = action_data.get("parameters", {})
            
            self.log_command(action_data, "")
            self.learning_patterns["usage_count"] = self.learning_patterns.get("usage_count", 0) + 1

            if action == "execute_command":
                command = parameters.get("command")
                # Confirmação para comandos sensíveis
                sensitive_reason = self._is_command_sensitive(command)
                if self.confirm_sensitive_commands and sensitive_reason:
                    confirmed = False
                    if self.confirmation_handler:
                        try:
                            confirmed = bool(self.confirmation_handler(command, sensitive_reason))
                        except Exception:
                            confirmed = False
                    if not confirmed:
                        self._update_action_pattern("execute_command", False)
                        return f"Comando sensível detectado e NÃO confirmado pelo usuário. Motivo: {sensitive_reason}"
                try:
                    if self.use_powershell and os.name == "nt":
                        result = subprocess.run([
                            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command
                        ], shell=False, capture_output=True, text=True, timeout=self.command_timeout, encoding='utf-8', errors='ignore')
                    else:
                        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=self.command_timeout, encoding='utf-8', errors='ignore')
                    output = f"Stdout:\n{result.stdout}\nStderr:\n{result.stderr}"
                    self.log_command(action_data, output)
                    self._update_action_pattern("execute_command", True)
                    return f"Comando executado com sucesso.\n{output}"
                except Exception as e:
                    self._update_action_pattern("execute_command", False)
                    return f"Erro ao executar comando: {e}"

            elif action == "read_file":
                path = Path(parameters.get("path"))
                if path.exists() and path.is_file():
                    try:
                        # Permite leitura de qualquer formato: texto com fallback de encoding, ou binário (base64)
                        as_text = bool(parameters.get("as_text"))
                        encoding = parameters.get("encoding")
                        max_bytes = int(parameters.get("max_bytes", 4096))
                        full_binary = bool(parameters.get("full_binary"))

                        # Se explicitamente solicitado texto ou fornecido encoding, tenta texto
                        if as_text or encoding:
                            try:
                                enc = encoding or 'utf-8'
                                content = path.read_text(encoding=enc, errors='ignore')
                                self._update_action_pattern("read_file", True)
                                return f"Conteúdo (texto, encoding {enc}) de '{path}':\n{content}"
                            except Exception as e:
                                # Fallback para binário se leitura de texto falhar
                                pass

                        # Tenta primeiro como texto UTF-8
                        try:
                            content = path.read_text(encoding='utf-8')
                            self._update_action_pattern("read_file", True)
                            return f"Conteúdo (texto, utf-8) de '{path}':\n{content}"
                        except UnicodeDecodeError:
                            # Não é texto UTF-8 — trata como binário
                            pass
                        except Exception:
                            # Pode ser outro erro — tenta binário
                            pass

                        # Leitura binária, retorna base64 (trecho ou completo)
                        total_size = path.stat().st_size
                        with open(path, 'rb') as f:
                            if full_binary:
                                data = f.read()
                                b64 = base64.b64encode(data).decode('ascii')
                                self._update_action_pattern("read_file", True)
                                return (
                                    f"Conteúdo binário de '{path}' (tamanho total {total_size} bytes).\n"
                                    f"Base64 ({len(data)} bytes):\n{b64}"
                                )
                            else:
                                chunk = f.read(max_bytes)
                                b64 = base64.b64encode(chunk).decode('ascii')
                                self._update_action_pattern("read_file", True)
                                return (
                                    f"Conteúdo binário de '{path}' (tamanho total {total_size} bytes).\n"
                                    f"Base64 dos primeiros {len(chunk)} bytes:\n{b64}"
                                )
                    except Exception as e:
                        self._update_action_pattern("read_file", False)
                        return f"Erro ao ler o arquivo: {e}"
                else:
                    self._update_action_pattern("read_file", False)
                    return f"Erro: Arquivo '{path}' não encontrado."

            elif action == "ingest_file":
                # Lê o arquivo por completo e grava conhecimento em warpclone_knowledge/ingested/*.md
                path = Path(parameters.get("path"))
                if not (path.exists() and path.is_file()):
                    self._update_action_pattern("ingest_file", False)
                    return f"Erro: Arquivo '{path}' não encontrado."
                try:
                    ingested_dir = self.knowledge_dir / "ingested"
                    ingested_dir.mkdir(parents=True, exist_ok=True)
                    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", path.name).strip("_") or "arquivo"
                    out_md = ingested_dir / f"{safe_name}.md"

                    # Tenta como texto completo
                    content_text = None
                    try:
                        content_text = path.read_text(encoding='utf-8')
                        as_text = True
                    except Exception:
                        # UTF-8 falhou, tenta encoding mais permissivo
                        try:
                            content_text = path.read_text(encoding='latin-1', errors='ignore')
                            as_text = True
                        except Exception:
                            as_text = False

                    if as_text and content_text is not None:
                        md = (
                            f"# Ingested: {path.name}\n\n"
                            f"- Caminho: `{path}`\n"
                            f"- Tamanho: {path.stat().st_size} bytes\n"
                            f"- Tipo: texto (utf-8/l1)\n\n"
                            f"## Conteúdo\n\n"
                            f"```\n{content_text}\n```\n"
                        )
                    else:
                        # Binário: grava base64 completo
                        data = path.read_bytes()
                        b64 = base64.b64encode(data).decode('ascii')
                        md = (
                            f"# Ingested: {path.name}\n\n"
                            f"- Caminho: `{path}`\n"
                            f"- Tamanho: {path.stat().st_size} bytes\n"
                            f"- Tipo: binário\n\n"
                            f"## Conteúdo (base64)\n\n"
                            f"```\n{b64}\n```\n"
                        )

                    out_md.write_text(md, encoding='utf-8')
                    self._update_action_pattern("ingest_file", True)
                    return f"Arquivo ingerido em '{out_md}'. O conteúdo agora faz parte do conhecimento local."
                except Exception as e:
                    self._update_action_pattern("ingest_file", False)
                    return f"Erro ao ingerir arquivo: {e}"

            elif action == "write_file":
                path = Path(parameters.get("path"))
                content = parameters.get("content")
                try:
                    path.write_text(content, encoding='utf-8')
                    self._update_action_pattern("write_file", True)
                    return f"Arquivo '{path}' salvo com sucesso."
                except Exception as e:
                    self._update_action_pattern("write_file", False)
                    return f"Erro ao salvar o arquivo: {e}"

            elif action == "create_file":
                path = Path(parameters.get("path"))
                content = parameters.get("content", "")
                try:
                    # Cria diretórios se necessário
                    if path.parent and not path.parent.exists():
                        path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding='utf-8')
                    self._update_action_pattern("create_file", True)
                    return f"Arquivo '{path}' criado com sucesso."
                except Exception as e:
                    self._update_action_pattern("create_file", False)
                    return f"Erro ao criar o arquivo: {e}"

            elif action == "delete_file":
                path = Path(parameters.get("path"))
                if not path.exists():
                    self._update_action_pattern("delete_file", False)
                    return f"Erro: Caminho '{path}' não existe."
                if path.is_dir():
                    self._update_action_pattern("delete_file", False)
                    return f"Erro: '{path}' é um diretório. Use uma ação específica para diretórios."
                # Confirmação opcional para deleção
                if self.confirm_sensitive_commands:
                    confirmed = False
                    if self.confirmation_handler:
                        try:
                            confirmed = bool(self.confirmation_handler(str(path), "Deleção de arquivo"))
                        except Exception:
                            confirmed = False
                    if not confirmed:
                        self._update_action_pattern("delete_file", False)
                        return f"Ação sensível não confirmada pelo usuário: deleção de '{path}'."
                try:
                    path.unlink()
                    self._update_action_pattern("delete_file", True)
                    return f"Arquivo '{path}' deletado com sucesso."
                except Exception as e:
                    self._update_action_pattern("delete_file", False)
                    return f"Erro ao deletar o arquivo: {e}"

            elif action == "list_dir":
                try:
                    base = Path(parameters.get("path", "."))
                    recursive = bool(parameters.get("recursive", False))
                    if not base.exists() or not base.is_dir():
                        self._update_action_pattern("list_dir", False)
                        return f"Erro: Diretório '{base}' inválido."
                    entries = []
                    if recursive:
                        for p in base.rglob("*"):
                            entries.append(str(p))
                    else:
                        entries = [str(p) for p in base.iterdir()]
                    self._update_action_pattern("list_dir", True)
                    return "Itens do diretório:\n" + "\n".join(entries)
                except Exception as e:
                    self._update_action_pattern("list_dir", False)
                    return f"Erro ao listar diretório: {e}"

            elif action == "create_dir":
                try:
                    path = Path(parameters.get("path"))
                    path.mkdir(parents=True, exist_ok=True)
                    self._update_action_pattern("create_dir", True)
                    return f"Diretório '{path}' criado/garantido com sucesso."
                except Exception as e:
                    self._update_action_pattern("create_dir", False)
                    return f"Erro ao criar diretório: {e}"

            elif action == "delete_dir":
                path = Path(parameters.get("path"))
                recursive = bool(parameters.get("recursive", True))
                if not path.exists() or not path.is_dir():
                    self._update_action_pattern("delete_dir", False)
                    return f"Erro: Diretório '{path}' inválido."
                # Confirmação
                if self.confirm_sensitive_commands:
                    confirmed = False
                    if self.confirmation_handler:
                        try:
                            reason = "Deleção de diretório (recursiva)" if recursive else "Deleção de diretório"
                            confirmed = bool(self.confirmation_handler(str(path), reason))
                        except Exception:
                            confirmed = False
                    if not confirmed:
                        self._update_action_pattern("delete_dir", False)
                        return f"Ação sensível não confirmada: deleção de diretório '{path}'."
                try:
                    if recursive:
                        shutil.rmtree(path)
                    else:
                        path.rmdir()
                    self._update_action_pattern("delete_dir", True)
                    return f"Diretório '{path}' removido com sucesso."
                except Exception as e:
                    self._update_action_pattern("delete_dir", False)
                    return f"Erro ao remover diretório: {e}"

            elif action == "copy_file":
                try:
                    src = Path(parameters.get("src"))
                    dst = Path(parameters.get("dst"))
                    if not src.exists() or not src.is_file():
                        self._update_action_pattern("copy_file", False)
                        return f"Erro: Origem '{src}' inválida."
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    self._update_action_pattern("copy_file", True)
                    return f"Arquivo copiado: '{src}' -> '{dst}'."
                except Exception as e:
                    self._update_action_pattern("copy_file", False)
                    return f"Erro ao copiar arquivo: {e}"

            elif action == "move_file":
                try:
                    src = Path(parameters.get("src"))
                    dst = Path(parameters.get("dst"))
                    if not src.exists():
                        self._update_action_pattern("move_file", False)
                        return f"Erro: Origem '{src}' não existe."
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dst))
                    self._update_action_pattern("move_file", True)
                    return f"Movido: '{src}' -> '{dst}'."
                except Exception as e:
                    self._update_action_pattern("move_file", False)
                    return f"Erro ao mover: {e}"

            elif action == "rename_file":
                try:
                    path = Path(parameters.get("path"))
                    new_path = Path(parameters.get("new_path"))
                    path.rename(new_path)
                    self._update_action_pattern("rename_file", True)
                    return f"Renomeado: '{path}' -> '{new_path}'."
                except Exception as e:
                    self._update_action_pattern("rename_file", False)
                    return f"Erro ao renomear: {e}"

            elif action == "append_file":
                try:
                    path = Path(parameters.get("path"))
                    content = parameters.get("content", "")
                    with open(path, "a", encoding="utf-8", errors="ignore") as f:
                        f.write(content)
                    self._update_action_pattern("append_file", True)
                    return f"Conteúdo anexado em '{path}'."
                except Exception as e:
                    self._update_action_pattern("append_file", False)
                    return f"Erro ao anexar conteúdo: {e}"

            elif action == "file_hash":
                try:
                    path = Path(parameters.get("path"))
                    algorithm = (parameters.get("algorithm") or "sha256").lower()
                    if not path.exists() or not path.is_file():
                        self._update_action_pattern("file_hash", False)
                        return f"Erro: Arquivo '{path}' inválido."
                    h = hashlib.new(algorithm)
                    with open(path, "rb") as f:
                        for chunk in iter(lambda: f.read(8192), b""):
                            h.update(chunk)
                    digest = h.hexdigest()
                    self._update_action_pattern("file_hash", True)
                    return f"Hash ({algorithm}) de '{path}': {digest}"
                except Exception as e:
                    self._update_action_pattern("file_hash", False)
                    return f"Erro ao calcular hash: {e}"

            elif action == "zip_create":
                try:
                    source = Path(parameters.get("source"))
                    zip_path = Path(parameters.get("zip_path"))
                    if not source.exists():
                        self._update_action_pattern("zip_create", False)
                        return f"Erro: Caminho de origem '{source}' não existe."
                    zip_path.parent.mkdir(parents=True, exist_ok=True)
                    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                        if source.is_dir():
                            for p in source.rglob("*"):
                                if p.is_file():
                                    zf.write(p, p.relative_to(source))
                        else:
                            zf.write(source, source.name)
                    self._update_action_pattern("zip_create", True)
                    return f"Arquivo ZIP criado em '{zip_path}'."
                except Exception as e:
                    self._update_action_pattern("zip_create", False)
                    return f"Erro ao criar ZIP: {e}"

            elif action == "zip_extract":
                try:
                    zip_path = Path(parameters.get("zip_path"))
                    dest = Path(parameters.get("dest"))
                    if not zip_path.exists() or not zipfile.is_zipfile(zip_path):
                        self._update_action_pattern("zip_extract", False)
                        return f"Erro: '{zip_path}' não é um ZIP válido."
                    dest.mkdir(parents=True, exist_ok=True)
                    with zipfile.ZipFile(zip_path, 'r') as zf:
                        zf.extractall(dest)
                    self._update_action_pattern("zip_extract", True)
                    return f"ZIP extraído para '{dest}'."
                except Exception as e:
                    self._update_action_pattern("zip_extract", False)
                    return f"Erro ao extrair ZIP: {e}"

            elif action == "download_file":
                try:
                    url = parameters.get("url")
                    dest = Path(parameters.get("dest"))
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
                    }
                    r = requests.get(url, stream=True, timeout=30, headers=headers)
                    r.raise_for_status()
                    with open(dest, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    self._update_action_pattern("download_file", True)
                    return f"Download concluído: {url} -> '{dest}'."
                except Exception as e:
                    self._update_action_pattern("download_file", False)
                    return f"Erro no download: {e}"

            elif action == "list_processes":
                try:
                    if not psutil:
                        self._update_action_pattern("list_processes", False)
                        return "psutil não disponível para listar processos."
                    top_n = int(parameters.get("top_n", 20))
                    plist = [p.info for p in psutil.process_iter(['pid','name','cpu_percent','memory_percent'])]
                    plist_sorted = sorted(plist, key=lambda x: (x.get('cpu_percent') or 0), reverse=True)[:top_n]
                    lines = [f"PID {p['pid']} {p['name']} | CPU {p.get('cpu_percent',0)}% | MEM {round(p.get('memory_percent',0),2)}%" for p in plist_sorted]
                    self._update_action_pattern("list_processes", True)
                    return "Processos (top CPU):\n" + "\n".join(lines)
                except Exception as e:
                    self._update_action_pattern("list_processes", False)
                    return f"Erro ao listar processos: {e}"

            elif action == "kill_process":
                try:
                    target_pid = parameters.get("pid")
                    target_name = parameters.get("name")
                    # Confirmação
                    if self.confirm_sensitive_commands:
                        confirmed = False
                        if self.confirmation_handler:
                            try:
                                label = f"Encerrar processo PID {target_pid}" if target_pid else f"Encerrar processo '{target_name}'"
                                confirmed = bool(self.confirmation_handler(label, "Encerramento de processo"))
                            except Exception:
                                confirmed = False
                        if not confirmed:
                            self._update_action_pattern("kill_process", False)
                            return "Ação sensível não confirmada: kill de processo."
                    if not psutil:
                        self._update_action_pattern("kill_process", False)
                        return "psutil não disponível para encerrar processos."
                    killed = []
                    if target_pid:
                        try:
                            p = psutil.Process(int(target_pid))
                            p.terminate()
                            killed.append(int(target_pid))
                        except Exception:
                            pass
                    elif target_name:
                        for p in psutil.process_iter(['pid','name']):
                            try:
                                if p.info.get('name','').lower() == target_name.lower():
                                    p.terminate()
                                    killed.append(p.info['pid'])
                            except Exception:
                                continue
                    self._update_action_pattern("kill_process", bool(killed))
                    return f"Processos encerrados: {killed}" if killed else "Nenhum processo encerrado."
                except Exception as e:
                    self._update_action_pattern("kill_process", False)
                    return f"Erro ao encerrar processo: {e}"

            elif action == "list_services":
                if os.name != "nt":
                    self._update_action_pattern("list_services", False)
                    return "Ação suportada apenas em Windows."
                try:
                    filter_str = parameters.get("filter", "")
                    ps_cmd = "Get-Service"
                    if filter_str:
                        ps_cmd += f" | Where-Object {{$_.Name -like '*{filter_str}*' -or $_.DisplayName -like '*{filter_str}*'}}"
                    ps_cmd += " | Select-Object Name, DisplayName, Status, StartType | Format-Table -AutoSize | Out-String"
                    result = subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-Command", ps_cmd],
                                            shell=False, capture_output=True, text=True, timeout=self.command_timeout, encoding='utf-8', errors='ignore')
                    self._update_action_pattern("list_services", True)
                    return result.stdout or ""
                except Exception as e:
                    self._update_action_pattern("list_services", False)
                    return f"Erro ao listar serviços: {e}"

            elif action == "start_service":
                if os.name != "nt":
                    self._update_action_pattern("start_service", False)
                    return "Ação suportada apenas em Windows."
                try:
                    name = parameters.get("name")
                    ps_cmd = f"Start-Service -Name '{name}' -ErrorAction SilentlyContinue"
                    result = subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-Command", ps_cmd],
                                            shell=False, capture_output=True, text=True, timeout=self.command_timeout, encoding='utf-8', errors='ignore')
                    self._update_action_pattern("start_service", True)
                    return result.stdout or f"Serviço '{name}' acionado para iniciar."
                except Exception as e:
                    self._update_action_pattern("start_service", False)
                    return f"Erro ao iniciar serviço: {e}"

            elif action == "stop_service":
                if os.name != "nt":
                    self._update_action_pattern("stop_service", False)
                    return "Ação suportada apenas em Windows."
                try:
                    name = parameters.get("name")
                    # Confirmação
                    if self.confirm_sensitive_commands:
                        confirmed = False
                        if self.confirmation_handler:
                            try:
                                confirmed = bool(self.confirmation_handler(name, "Parar serviço"))
                            except Exception:
                                confirmed = False
                        if not confirmed:
                            self._update_action_pattern("stop_service", False)
                            return "Ação sensível não confirmada: parar serviço."
                    ps_cmd = f"Stop-Service -Name '{name}' -Force -ErrorAction SilentlyContinue"
                    result = subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-Command", ps_cmd],
                                            shell=False, capture_output=True, text=True, timeout=self.command_timeout, encoding='utf-8', errors='ignore')
                    self._update_action_pattern("stop_service", True)
                    return result.stdout or f"Serviço '{name}' acionado para parar."
                except Exception as e:
                    self._update_action_pattern("stop_service", False)
                    return f"Erro ao parar serviço: {e}"

            elif action == "list_scheduled_tasks":
                if os.name != "nt":
                    self._update_action_pattern("list_scheduled_tasks", False)
                    return "Ação suportada apenas em Windows."
                try:
                    path = parameters.get("path")
                    ps_cmd = "Get-ScheduledTask"
                    if path:
                        ps_cmd += f" -TaskPath '{path}'"
                    ps_cmd += " | Select-Object TaskName, TaskPath, State | Format-Table -AutoSize | Out-String"
                    result = subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-Command", ps_cmd],
                                            shell=False, capture_output=True, text=True, timeout=self.command_timeout, encoding='utf-8', errors='ignore')
                    self._update_action_pattern("list_scheduled_tasks", True)
                    return result.stdout or ""
                except Exception as e:
                    self._update_action_pattern("list_scheduled_tasks", False)
                    return f"Erro ao listar tarefas agendadas: {e}"

            elif action == "list_network_connections":
                try:
                    if not psutil:
                        self._update_action_pattern("list_network_connections", False)
                        return "psutil não disponível."
                    conns = psutil.net_connections(kind='inet')
                    lines = []
                    for c in conns[:200]:
                        laddr = f"{getattr(c.laddr,'ip',None)}:{getattr(c.laddr,'port',None)}" if c.laddr else ""
                        raddr = f"{getattr(c.raddr,'ip',None)}:{getattr(c.raddr,'port',None)}" if c.raddr else ""
                        lines.append(f"{c.type} {c.status} {laddr} -> {raddr}")
                    self._update_action_pattern("list_network_connections", True)
                    return "Conexões de rede (amostra):\n" + "\n".join(lines)
                except Exception as e:
                    self._update_action_pattern("list_network_connections", False)
                    return f"Erro ao listar conexões: {e}"

            elif action == "open_ports":
                try:
                    if not psutil:
                        self._update_action_pattern("open_ports", False)
                        return "psutil não disponível."
                    conns = psutil.net_connections(kind='inet')
                    listens = [c for c in conns if getattr(c,'status',None) == psutil.CONN_LISTEN]
                    lines = []
                    for c in listens[:100]:
                        laddr = f"{getattr(c.laddr,'ip',None)}:{getattr(c.laddr,'port',None)}" if c.laddr else ""
                        lines.append(laddr)
                    self._update_action_pattern("open_ports", True)
                    return "Portas em escuta:\n" + "\n".join(lines)
                except Exception as e:
                    self._update_action_pattern("open_ports", False)
                    return f"Erro ao listar portas: {e}"

            elif action == "firewall_state":
                if os.name != "nt":
                    self._update_action_pattern("firewall_state", False)
                    return "Ação suportada apenas em Windows."
                try:
                    ps_cmd = "netsh advfirewall show allprofiles | Select-String 'State'"
                    result = subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-Command", ps_cmd],
                                            shell=False, capture_output=True, text=True, timeout=self.command_timeout, encoding='utf-8', errors='ignore')
                    self._update_action_pattern("firewall_state", True)
                    return result.stdout or ""
                except Exception as e:
                    self._update_action_pattern("firewall_state", False)
                    return f"Erro ao checar firewall: {e}"

            elif action == "ping_host":
                try:
                    host = parameters.get("host")
                    count = int(parameters.get("count", 4))
                    cmd = f"ping -n {count} {host}" if os.name == "nt" else f"ping -c {count} {host}"
                    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=self.command_timeout, encoding='utf-8', errors='ignore')
                    self._update_action_pattern("ping_host", True)
                    return result.stdout or ""
                except Exception as e:
                    self._update_action_pattern("ping_host", False)
                    return f"Erro no ping: {e}"

            elif action == "traceroute_host":
                try:
                    host = parameters.get("host")
                    cmd = f"tracert {host}" if os.name == "nt" else f"traceroute {host}"
                    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=max(self.command_timeout, 60), encoding='utf-8', errors='ignore')
                    self._update_action_pattern("traceroute_host", True)
                    return result.stdout or ""
                except Exception as e:
                    self._update_action_pattern("traceroute_host", False)
                    return f"Erro no traceroute: {e}"

            elif action == "get_env":
                try:
                    name = parameters.get("name")
                    val = os.environ.get(name, "")
                    self._update_action_pattern("get_env", True)
                    return f"Variável '{name}': {val}"
                except Exception as e:
                    self._update_action_pattern("get_env", False)
                    return f"Erro ao obter variável: {e}"

            elif action == "set_env":
                try:
                    name = parameters.get("name")
                    value = parameters.get("value")
                    # Confirmação opcional
                    if self.confirm_sensitive_commands:
                        confirmed = False
                        if self.confirmation_handler:
                            try:
                                confirmed = bool(self.confirmation_handler(f"{name}={value}", "Definir variável de ambiente"))
                            except Exception:
                                confirmed = False
                        if not confirmed:
                            self._update_action_pattern("set_env", False)
                            return "Ação sensível não confirmada: set de variável."
                    os.environ[name] = str(value)
                    self._update_action_pattern("set_env", True)
                    return f"Variável definida: {name}={value} (escopo do processo)"
                except Exception as e:
                    self._update_action_pattern("set_env", False)
                    return f"Erro ao definir variável: {e}"

            elif action == "read_registry":
                if os.name != "nt":
                    self._update_action_pattern("read_registry", False)
                    return "Ação suportada apenas em Windows."
                try:
                    path = parameters.get("path")
                    ps_cmd = f"Get-ItemProperty -Path '{path}' -ErrorAction SilentlyContinue | Format-List | Out-String"
                    result = subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-Command", ps_cmd],
                                            shell=False, capture_output=True, text=True, timeout=self.command_timeout, encoding='utf-8', errors='ignore')
                    self._update_action_pattern("read_registry", True)
                    return result.stdout or ""
                except Exception as e:
                    self._update_action_pattern("read_registry", False)
                    return f"Erro ao ler registro: {e}"

            elif action == "write_registry":
                if os.name != "nt":
                    self._update_action_pattern("write_registry", False)
                    return "Ação suportada apenas em Windows."
                try:
                    path = parameters.get("path")
                    name = parameters.get("name")
                    value = parameters.get("value")
                    dtype = (parameters.get("type") or "String")
                    # Confirmação
                    if self.confirm_sensitive_commands:
                        confirmed = False
                        if self.confirmation_handler:
                            try:
                                confirmed = bool(self.confirmation_handler(f"{path}::{name}={value} ({dtype})", "Escrita no registro"))
                            except Exception:
                                confirmed = False
                        if not confirmed:
                            self._update_action_pattern("write_registry", False)
                            return "Ação sensível não confirmada: escrita no registro."
                    ps_cmd = f"New-Item -Path '{path}' -Force | Out-Null; New-ItemProperty -Path '{path}' -Name '{name}' -Value '{value}' -PropertyType {dtype} -Force"
                    result = subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-Command", ps_cmd],
                                            shell=False, capture_output=True, text=True, timeout=self.command_timeout, encoding='utf-8', errors='ignore')
                    self._update_action_pattern("write_registry", True)
                    return result.stdout or "Registro atualizado com sucesso."
                except Exception as e:
                    self._update_action_pattern("write_registry", False)
                    return f"Erro ao escrever no registro: {e}"

            elif action == "search_regex":
                try:
                    pattern = parameters.get("pattern")
                    ext = parameters.get("extension", None)
                    flags = re.MULTILINE
                    rx = re.compile(pattern, flags)
                    results = []
                    for p in Path.cwd().rglob("*" + (ext or "")):
                        if p.is_file():
                            try:
                                text = p.read_text(encoding='utf-8', errors='ignore')
                            except Exception:
                                continue
                            for m in rx.finditer(text):
                                line_no = text.count("\n", 0, m.start()) + 1
                                snippet = text[max(0, m.start()-40):m.end()+40].replace("\n"," ")
                                results.append(f"{p}:{line_no}: {snippet}")
                                if len(results) >= 200:
                                    break
                    self._update_action_pattern("search_regex", bool(results))
                    return "Resultados de regex:\n" + "\n".join(results) if results else "Nenhuma correspondência."
                except Exception as e:
                    self._update_action_pattern("search_regex", False)
                    return f"Erro na busca regex: {e}"

            elif action == "search_files":
                pattern = parameters.get("pattern")
                files = [str(p) for p in Path.cwd().rglob(pattern)]
                self._update_action_pattern("search_files", True)
                return f"Arquivos encontrados para o padrão '{pattern}':\n" + "\n".join(files)

            elif action == "search_content":
                term = parameters.get("term")
                ext = parameters.get("extension", ".*")
                results = []
                for p in Path.cwd().rglob(f"*{ext}"):
                    if p.is_file():
                        try:
                            content = p.read_text(encoding='utf-8')
                            if term in content:
                                results.append(f"- {p}:\n{content.splitlines()[0]}...")
                        except (UnicodeDecodeError, IOError):
                            continue
                success = len(results) > 0
                self._update_action_pattern("search_content", success)
                return f"Resultados da busca por '{term}':\n" + "\n".join(results) if results else "Nenhum resultado encontrado."

            elif action == "knowledge_search":
                query = parameters.get("query", "")
                top_k = int(parameters.get("top_k", 5))
                results = self._knowledge_search(query, top_k=top_k)
                success = len(results) > 0
                self._update_action_pattern("knowledge_search", success)
                if success:
                    formatted = "\n".join([f"- {r['file']}\n  {r['snippet']}" for r in results])
                    return f"Resultados da base de conhecimento para '{query}':\n" + formatted
                else:
                    return f"Nenhum conhecimento relevante encontrado para '{query}'."

            elif action == "analyze_system":
                try:
                    import platform, socket
                except Exception:
                    platform = None
                    socket = None

                lines = []
                # Informações do SO
                try:
                    if platform:
                        lines.append(f"- SO: {platform.system()} {platform.release()} ({platform.version()})")
                        lines.append(f"- Build: {platform.platform()}")
                        lines.append(f"- Arquitetura: {platform.machine()}")
                    if socket:
                        lines.append(f"- Hostname: {socket.gethostname()}")
                except Exception:
                    pass

                # CPU e memória
                if psutil:
                    try:
                        cpu_usage = psutil.cpu_percent(interval=0.5)
                    except Exception:
                        cpu_usage = psutil.cpu_percent() if psutil else "N/D"
                    try:
                        cores = psutil.cpu_count(logical=False) or 0
                        threads = psutil.cpu_count() or 0
                        freq = psutil.cpu_freq()
                        freq_txt = f" | Freq: {int(freq.current)} MHz" if freq else ""
                        lines.append(f"- CPU: {cpu_usage}% uso | Cores: {cores} | Threads: {threads}{freq_txt}")
                    except Exception:
                        lines.append(f"- CPU: {cpu_usage}% uso")

                    try:
                        vm = psutil.virtual_memory()
                        lines.append(f"- Memória: {vm.percent}% uso | Total: {vm.total // (1024**3)} GB | Livre: {vm.available // (1024**3)} GB")
                    except Exception:
                        pass

                    # Discos
                    try:
                        disk_lines = []
                        for p in psutil.disk_partitions(all=False):
                            try:
                                u = psutil.disk_usage(p.mountpoint)
                                disk_lines.append(f"{p.device} ({p.mountpoint}) {u.percent}% usado de {u.total // (1024**3)} GB")
                            except Exception:
                                continue
                        if disk_lines:
                            lines.append("- Discos:\n  " + "\n  ".join(disk_lines))
                    except Exception:
                        pass

                    # Rede
                    try:
                        import socket as _socket
                        addr_lines = []
                        for iface, addrlist in psutil.net_if_addrs().items():
                            for a in addrlist:
                                try:
                                    if hasattr(_socket, "AF_INET") and a.family == _socket.AF_INET:
                                        ip = getattr(a, "address", "")
                                        if ip and not ip.startswith("127."):
                                            addr_lines.append(f"{iface}: {ip}")
                                except Exception:
                                    continue
                        if addr_lines:
                            lines.append("- Endereços IPv4:\n  " + "\n  ".join(addr_lines))
                    except Exception:
                        pass

                    # Top processos por CPU (amostra rápida)
                    try:
                        procs = [p.info for p in psutil.process_iter(['pid','name','cpu_percent','memory_percent'])]
                        procs_sorted = sorted(procs, key=lambda x: x.get('cpu_percent') or 0, reverse=True)[:8]
                        proc_lines = [f"PID {p['pid']} {p['name']} | CPU {p.get('cpu_percent', 0)}% | MEM {round(p.get('memory_percent', 0),2)}%" for p in procs_sorted]
                        if proc_lines:
                            lines.append("- Processos (top CPU):\n  " + "\n  ".join(proc_lines))
                    except Exception:
                        pass

                # Lista básica de arquivos no diretório atual
                try:
                    files = [str(p) for p in Path.cwd().iterdir()]
                    lines.append("- Arquivos no diretório atual:\n  " + "\n  ".join(files))
                except Exception:
                    pass

                # Telemetria (Windows): serviços, tarefas agendadas e registro
                telemetry_block = ""
                try:
                    if os.name == "nt":
                        ps_lines = [
                            "$svc = Get-Service diagtrack, dmwappushservice -ErrorAction SilentlyContinue | Select-Object Name, Status, StartType | Format-Table -AutoSize | Out-String",
                            "$tele = Get-Service | Where-Object {$_.Name -like '*telemetry*' -or $_.Name -like '*diagtrack*' -or $_.Name -like '*dmwappush*'} | Select-Object Name, Status, StartType | Format-Table -AutoSize | Out-String",
                            "$tasks1 = Get-ScheduledTask -TaskPath '\\Microsoft\\Windows\\Customer Experience Improvement Program\\' -ErrorAction SilentlyContinue | Select-Object TaskName, State | Format-Table -AutoSize | Out-String",
                            "$tasks2 = Get-ScheduledTask -TaskPath '\\Microsoft\\Windows\\Application Experience\\' -ErrorAction SilentlyContinue | Select-Object TaskName, State | Format-Table -AutoSize | Out-String",
                            "$reg1 = (Get-ItemProperty -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\DataCollection' -ErrorAction SilentlyContinue | Format-List | Out-String)",
                            "$reg2 = (Get-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Privacy' -ErrorAction SilentlyContinue | Format-List | Out-String)",
                            "Write-Output '--- Telemetria: Serviços (DiagTrack/dmwappush) ---'",
                            "Write-Output $svc",
                            "Write-Output '--- Telemetria: Serviços relacionados ---'",
                            "Write-Output $tele",
                            "Write-Output '--- Telemetria: Tarefas CEIP ---'",
                            "Write-Output $tasks1",
                            "Write-Output '--- Telemetria: Tarefas App Experience ---'",
                            "Write-Output $tasks2",
                            "Write-Output '--- Telemetria: Registro (DataCollection) ---'",
                            "Write-Output $reg1",
                            "Write-Output '--- Telemetria: Registro (Privacy) ---'",
                            "Write-Output $reg2"
                        ]
                        ps_cmd = "; ".join(ps_lines)
                        result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
                                                 shell=False, capture_output=True, text=True, timeout=self.command_timeout, encoding='utf-8', errors='ignore')
                        telemetry_block = (result.stdout or "").strip()
                except Exception:
                    telemetry_block = ""

                self._update_action_pattern("analyze_system", True)
                report = "Análise detalhada do Sistema:\n" + "\n".join(lines)
                if telemetry_block:
                    report += "\n\nTelemetria (Windows):\n" + telemetry_block
                return report

            elif action == "web_search":
                query = parameters.get("query", "")
                try:
                    num = int(parameters.get("num", 5))
                except Exception:
                    num = 5
                results = self._web_search_duckduckgo(query, max_results=num)
                success = len(results) > 0
                self._update_action_pattern("web_search", success)
                if success:
                    formatted = "\n".join([f"- {r['title']}\n  {r['url']}" for r in results])
                    return f"Resultados da web para '{query}':\n" + formatted
                else:
                    return f"Nenhum resultado encontrado para '{query}'."

            elif action == "fetch_url":
                url = parameters.get("url", "")
                try:
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
                    }
                    resp = requests.get(url, timeout=12, headers=headers)
                    resp.raise_for_status()
                    content = resp.text
                    snippet = content[:2000]
                    self._update_action_pattern("fetch_url", True)
                    return f"Conteúdo obtido de {url}:\n{snippet}\n..."
                except Exception as e:
                    self._update_action_pattern("fetch_url", False)
                    return f"Erro ao buscar URL '{url}': {e}"

            elif action == "answer":
                self._update_action_pattern("answer", True)
                return f"FINAL_ANSWER:{parameters.get('answer')}"
                
            else:
                return f"Erro: Ação '{action}' desconhecida."

        except json.JSONDecodeError:
            # Mantém compatibilidade, mas agora já existe fallback acima
            return f"Erro: A resposta do modelo não é um JSON válido."
        except Exception as e:
            return f"Erro ao executar a ação: {e}"

    def execute_task(self, task, max_iterations=5, max_runtime_sec=90):
        start_ts = time.time()
        # Em modo offline, aumentamos o teto de iterações para permitir planos multi-etapas
        try:
            if not self.llm_enabled or getattr(self, "offline_mode", False):
                max_iterations = max(max_iterations, 6)
        except Exception:
            pass
        current_task = task
        last_action_result = None
        for i in range(max_iterations):
            print(f"--- Iteração {i+1} ---")
            
            action_json = self.call_ollama(current_task)
            
            try:
                parsed = self._safe_json_loads(action_json)
                thought = (parsed or {}).get("thought", "")
                if thought:
                    self.memory["short_term"].append(thought)
            except Exception:
                pass

            result = self.execute_action(action_json)
            
            if result.startswith("FINAL_ANSWER:"):
                final_answer = result.replace("FINAL_ANSWER:", "").strip()
                self.memory["short_term"].append(f"Tarefa concluída: {final_answer}")
                self.learning_patterns["last_success"] = final_answer
                self.save_memory()
                # Persiste resposta final no histórico da sessão
                try:
                    self.conversation_history.append({"role": "assistant", "content": final_answer})
                    self.save_session()
                except Exception:
                    pass
                return final_answer, last_action_result
            
            last_action_result = result
            # Se houver um plano offline ativo, acumula saída do passo
            try:
                if hasattr(self, "_offline_plan") and self._offline_plan is not None:
                    self._offline_plan.setdefault("outputs", []).append(str(result))
            except Exception:
                pass
            current_task = f"A ação anterior retornou o seguinte resultado:\n{result}\n\nCom base nisso, qual o próximo passo para completar a tarefa original: '{task}'?"
            self.conversation_history.append({"role": "user", "content": current_task})
            self.save_session()
            # Guarda de tempo total para evitar travas prolongadas
            if time.time() - start_ts > max_runtime_sec:
                final_answer = "Tempo limite excedido ao tentar concluir a tarefa."
                self.conversation_history.append({"role": "assistant", "content": final_answer})
                self.save_session()
                self.memory["short_term"].append(final_answer)
                self.save_memory()
                return final_answer, last_action_result

        final_answer = "Não foi possível concluir a tarefa após o número máximo de iterações."
        self.memory["short_term"].append(final_answer)
        self.save_memory()
        return final_answer, last_action_result

    def _update_action_pattern(self, action_name, success):
        actions = self.learning_patterns.setdefault("actions", {})
        action_stats = actions.setdefault(action_name, {"count": 0, "success": 0, "failure": 0})
        action_stats["count"] += 1
        if success:
            action_stats["success"] += 1
        else:
            action_stats["failure"] += 1

    def _web_search_duckduckgo(self, query, max_results=5):
        """Busca no DuckDuckGo via página HTML, com parsing robusto.
        - Suporta classes "result__a" e títulos em cabeçalhos.
        - Decodifica redirecionamentos "/l/?uddg=..." para URL final.
        """
        try:
            if not query:
                return []
            q = quote_plus(query)
            url = f"https://duckduckgo.com/html/?q={q}&kp=1"
            resp = requests.get(url, timeout=12, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
            })
            resp.raise_for_status()
            html = resp.text
            results = []
            import re
            from urllib.parse import urljoin, urlparse, parse_qs

            def _clean_text(t):
                return re.sub(r"<.*?>", "", t or "").strip()

            def _decode_ddg_href(href: str) -> str:
                try:
                    if not href:
                        return ""
                    full = urljoin("https://duckduckgo.com", href)
                    u = urlparse(full)
                    qs = parse_qs(u.query)
                    target = qs.get("uddg", [None])[0]
                    if target:
                        return target
                    return full if full.startswith("http") else href
                except Exception:
                    return href

            # Prefer anchors com class result__a
            for m in re.finditer(r'<a[^>]*class=["\']result__a["\'][^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, flags=re.IGNORECASE):
                href = m.group(1)
                title_html = m.group(2)
                title = _clean_text(title_html)
                url_final = _decode_ddg_href(href)
                results.append({"title": title, "url": url_final})
                if len(results) >= max_results:
                    return results

            # Fallback: anchors em cabeçalhos (títulos)
            for m in re.finditer(r'<h2[^>]*>\s*<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>\s*</h2>', html, flags=re.IGNORECASE):
                href = m.group(1)
                title_html = m.group(2)
                title = _clean_text(title_html)
                url_final = _decode_ddg_href(href)
                if not any(r["url"] == url_final for r in results):
                    results.append({"title": title, "url": url_final})
                    if len(results) >= max_results:
                        return results

            return results
        except Exception:
            return []

    def _is_command_sensitive(self, command: str):
        """Retorna uma razão se o comando for sensível, senão None."""
        if not command:
            return None
        cmd_lower = command.lower()
        patterns = [
            # Windows perigosos
            "shutdown", "format", "bcdedit", "reg add", "reg delete", "diskpart",
            "del ", "erase ", "rmdir /s", "rd /s", "cipher /w", "sdelete",
            "net user", "net localgroup", "netsh ", "sc stop", "sc delete", "taskkill",
            "wmic shadowcopy", "wmic process delete", "takeown", "icacls",
            "remove-appxpackage",
            # PowerShell perigosos
            "remove-item", "clear-content", "stop-service", "disable-windowsoptionalfeature",
            # Unix-like (no Windows via subsistema/compatibilidade)
            "rm -rf", "mkfs", "mount ", "umount ", "useradd ", "groupadd ", "chmod -R", "chown -R"
        ]
        for p in patterns:
            if p in cmd_lower:
                return f"Correspondência ao padrão '{p}'"
        # Heurística: comandos que escrevem em diretórios críticos
        critical_paths = ["c:/windows", "c:\\windows", "/windows", "c:/", "c:\\"]
        for cp in critical_paths:
            if cp in cmd_lower:
                return f"Atinge caminho crítico '{cp}'"
        return None

    def _knowledge_search(self, query: str, top_k: int = 5):
        """Busca simples por termos na pasta de conhecimento local e retorna trechos relevantes."""
        if not self.knowledge_dir.exists():
            return []
        results = []
        try:
            for p in self.knowledge_dir.rglob("*.md"):
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                score = text.lower().count(query.lower()) if query else 0
                if score > 0:
                    snippet = text[:500].replace("\n", " ")
                    results.append({"file": str(p), "score": score, "snippet": snippet})
            results.sort(key=lambda x: x["score"], reverse=True)
            return results[:top_k]
        except Exception:
            return []

    # --- Ollama helpers ---
    def _ollama_base_url(self) -> str:
        """Extrai host:porta da URL configurada para o Ollama."""
        try:
            u = urlparse(self.ollama_url or "http://localhost:11434")
            if u.scheme and u.netloc:
                return f"{u.scheme}://{u.netloc}"
        except Exception:
            pass
        return "http://localhost:11434"
    def _ollama_cli_available(self) -> bool:
        try:
            if shutil.which("ollama") is None:
                return False
            r = subprocess.run(["ollama", "version"], capture_output=True, text=True)
            return r.returncode == 0
        except Exception:
            return False

    def _ollama_health_check(self, force: bool = False) -> bool:
        """Verifica se o LLM está disponível com caching para evitar bloqueios repetidos."""
        if self.offline_mode:
            return False
        now = time.time()
        if not force and (now - self._ollama_last_check) < self._ollama_check_interval:
            return bool(self.ollama_available and self.llm_enabled)
        available = False
        try:
            if self._is_ollama_running():
                available = True
            else:
                available = False
        except Exception:
            available = False
        self.ollama_available = available
        self.llm_enabled = available
        self._ollama_last_check = now
        return available

    def _offline_decide_action(self, task: str) -> str:
        """Heurística offline com planos multi-etapas para tarefas comuns."""
        try:
            # Inicialização de estado do plano offline
            if not hasattr(self, "_offline_plan"):
                self._offline_plan = None

            text = (task or "")
            t = text.lower()

            # Se existir correspondência na biblioteca de comandos e nenhum plano em andamento, cria um plano
            lib_item = self._match_command_library(t)
            if lib_item and not self._offline_plan:
                steps = []
                for st in lib_item.get("plan", []):
                    steps.append({
                        "label": st.get("label", lib_item.get("title", "cmd")),
                        "ps": bool(st.get("powershell", False)),
                        "command": st.get("command", "")
                    })
                self._offline_plan = {
                    "name": lib_item.get("id", "custom_plan"),
                    "steps": steps,
                    "index": 0,
                    "outputs": [],
                    "needs_confirmation": bool(lib_item.get("confirmation", False))
                }
                # Se algum passo exige PowerShell, força PS em Windows
                if os.name == "nt" and any(s.get("ps") for s in steps):
                    self.use_powershell = True

            # Execução genérica de plano (não-hardware)
            if self._offline_plan and self._offline_plan.get("name") not in (None, "hardware_audit"):
                idx = self._offline_plan["index"]
                steps = self._offline_plan["steps"]
                if idx < len(steps):
                    step = steps[idx]
                    thought = f"Modo offline: executando passo '{step['label']}'."
                    return json.dumps({
                        "thought": thought,
                        "action": "execute_command",
                        "parameters": {"command": step["command"]}
                    }, ensure_ascii=False)
                else:
                    summary = "Plano concluído com sucesso. Consulte as saídas acima para detalhes."
                    self._offline_plan = None
                    return json.dumps({
                        "thought": "Todos os passos do plano foram executados.",
                        "action": "answer",
                        "parameters": {"answer": summary}
                    }, ensure_ascii=False)

            # Se recebemos resultado da ação anterior, atualiza o plano
            if "a ação anterior retornou o seguinte resultado:" in t:
                if self._offline_plan:
                    try:
                        # Extrai bloco após o prefixo padrão
                        prefix = "a ação anterior retornou o seguinte resultado:\n"
                        idx = t.find(prefix)
                        last_output = text[idx + len(prefix):] if idx != -1 else text
                        # Registra saída do passo
                        self._offline_plan.setdefault("outputs", []).append(last_output)
                        # Avança para próximo passo
                        self._offline_plan["index"] += 1
                    except Exception:
                        # Mesmo em falha, tenta avançar
                        self._offline_plan["index"] += 1
                else:
                    # Se não há plano e recebemos resultado, assumimos conclusão para comandos simples
                    return json.dumps({
                        "thought": "Recebi o resultado da ação anterior e não há plano pendente. Finalizando.",
                        "action": "answer",
                        "parameters": {"answer": "Ação executada com sucesso. Verifique o resultado acima."}
                    }, ensure_ascii=False)

            # Detecta intenção: auditoria de hardware e ano de fabricação
            if any(k in t for k in [
                "caracteristicas da maquina", "características da máquina", "detalhes da maquina",
                "ano de fabricação", "ano de fabricacao", "especificações", "hardware"
            ]):
                if not self._offline_plan:
                    # Monta plano multi-etapas com PowerShell (mais robusto que WMIC)
                    steps = [
                        {"label": "systeminfo+cpu+mem", "ps": True, "command": "(systeminfo | Out-String -Width 300); (Get-CimInstance Win32_Processor | Select-Object Name, Manufacturer, MaxClockSpeed, NumberOfCores, NumberOfLogicalProcessors | Out-String -Width 300); (Get-CimInstance Win32_PhysicalMemory | Select-Object Manufacturer, PartNumber, SerialNumber, Capacity, Speed | Out-String -Width 300)"},
                        {"label": "disks+gpu", "ps": True, "command": "(Get-CimInstance Win32_DiskDrive | Select-Object Model, Size, InterfaceType, MediaType, SerialNumber | Out-String -Width 300); (Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM, DriverVersion, VideoProcessor | Out-String -Width 300)"},
                        {"label": "board+bios", "ps": True, "command": "(Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer, Product, SerialNumber, Version | Out-String -Width 300); (Get-CimInstance Win32_BIOS | Select-Object Manufacturer, SMBIOSBIOSVersion, ReleaseDate | Out-String -Width 300)"}
                    ]
                    self._offline_plan = {"name": "hardware_audit", "steps": steps, "index": 0, "outputs": []}
                    # Para robustez em Windows, ativa powershell para comandos PS
                    if os.name == "nt":
                        self.use_powershell = True
                # Executa próximo passo ou retorna resumo
                idx = self._offline_plan["index"]
                steps = self._offline_plan["steps"]
                if idx < len(steps):
                    step = steps[idx]
                    thought = f"Modo offline: coletando informações de {step['label']}."
                    return json.dumps({
                        "thought": thought,
                        "action": "execute_command",
                        "parameters": {"command": step["command"]}
                    }, ensure_ascii=False)
                else:
                    # Finaliza com um resumo e estimativa de ano baseada no BIOS ReleaseDate
                    year_hint = self._offline_estimate_year(self._offline_plan.get("outputs", []))
                    summary = self._offline_summarize_hardware(self._offline_plan.get("outputs", []), year_hint)
                    # Limpa plano
                    self._offline_plan = None
                    return json.dumps({
                        "thought": "Coleta concluída. Apresentando resumo das características e ano estimado.",
                        "action": "answer",
                        "parameters": {"answer": summary}
                    }, ensure_ascii=False)

            # Criar arquivo
            if any(k in t for k in ["criar arquivo", "create file", "novo arquivo"]):
                # Usa a primeira string entre aspas como caminho e a segunda (se houver) como conteúdo
                quotes = re.findall(r"\"([^\"]+)\"", task or "")
                if quotes:
                    path = quotes[0]
                    content = quotes[1] if len(quotes) > 1 else ""
                    return json.dumps({
                        "thought": "Modo offline: vou criar o arquivo solicitado.",
                        "action": "create_file",
                        "parameters": {"path": path, "content": content}
                    }, ensure_ascii=False)
                else:
                    return json.dumps({
                        "thought": "Sem caminho informado para criação de arquivo.",
                        "action": "answer",
                        "parameters": {"answer": "Informe o caminho do arquivo entre aspas, por exemplo: criar arquivo \"C:/temp/novo.txt\" \"conteúdo\"."}
                    }, ensure_ascii=False)

            # Deletar arquivo
            if any(k in t for k in ["deletar arquivo", "apagar arquivo", "remover arquivo", "delete file", "remove file"]):
                m = re.search(r'\"([^\"]+)\"', task or "")
                if m:
                    path = m.group(1)
                    return json.dumps({
                        "thought": "Modo offline: vou solicitar deleção do arquivo informado.",
                        "action": "delete_file",
                        "parameters": {"path": path}
                    }, ensure_ascii=False)
                else:
                    return json.dumps({
                        "thought": "Sem caminho informado para deleção de arquivo.",
                        "action": "answer",
                        "parameters": {"answer": "Informe o caminho do arquivo entre aspas, por exemplo: deletar arquivo \"C:/temp/antigo.log\"."}
                    }, ensure_ascii=False)

            # Listar diretório com caminho
            if any(k in t for k in ["listar diretório", "listar diretorio", "listar pasta", "list directory"]):
                m = re.search(r'\"([^\"]+)\"', task or "")
                recursive = bool("recurs" in t)
                if m:
                    return json.dumps({
                        "thought": "Modo offline: listando itens do diretório especificado.",
                        "action": "list_dir",
                        "parameters": {"path": m.group(1), "recursive": recursive}
                    }, ensure_ascii=False)
                else:
                    return json.dumps({
                        "thought": "Sem caminho informado para listar diretório.",
                        "action": "answer",
                        "parameters": {"answer": "Informe o caminho do diretório entre aspas, ex.: listar diretório \"C:/temp\"."}
                    }, ensure_ascii=False)

            # Criar diretório
            if any(k in t for k in ["criar diretório", "criar diretorio", "create directory", "make dir", "mkdir"]):
                m = re.findall(r'\"([^\"]+)\"', task or "")
                if m:
                    return json.dumps({
                        "thought": "Modo offline: criando diretório solicitado.",
                        "action": "create_dir",
                        "parameters": {"path": m[0]}
                    }, ensure_ascii=False)
                else:
                    return json.dumps({
                        "thought": "Sem caminho informado para criação de diretório.",
                        "action": "answer",
                        "parameters": {"answer": "Informe o caminho do diretório entre aspas, ex.: criar diretório \"C:/temp/novo\"."}
                    }, ensure_ascii=False)

            # Deletar diretório
            if any(k in t for k in ["deletar diretório", "apagar diretório", "remover diretório", "delete directory", "remove directory"]):
                m = re.findall(r'\"([^\"]+)\"', task or "")
                recursive = bool("recurs" in t or "tudo" in t or "conteúdo" in t)
                if m:
                    return json.dumps({
                        "thought": "Modo offline: solicitando deleção do diretório informado.",
                        "action": "delete_dir",
                        "parameters": {"path": m[0], "recursive": recursive}
                    }, ensure_ascii=False)
                else:
                    return json.dumps({
                        "thought": "Sem caminho informado para deleção de diretório.",
                        "action": "answer",
                        "parameters": {"answer": "Informe o caminho do diretório entre aspas, ex.: deletar diretório \"C:/temp/antigo\"."}
                    }, ensure_ascii=False)

            # Copiar arquivo
            if any(k in t for k in ["copiar arquivo", "copy file"]):
                quotes = re.findall(r'\"([^\"]+)\"', task or "")
                if len(quotes) >= 2:
                    return json.dumps({
                        "thought": "Modo offline: copiando arquivo de origem para destino.",
                        "action": "copy_file",
                        "parameters": {"src": quotes[0], "dst": quotes[1]}
                    }, ensure_ascii=False)
                else:
                    return json.dumps({
                        "thought": "Faltam origem e destino para cópia.",
                        "action": "answer",
                        "parameters": {"answer": "Use duas aspas: copiar arquivo \"C:/a.txt\" \"C:/b.txt\"."}
                    }, ensure_ascii=False)

            # Mover arquivo
            if any(k in t for k in ["mover arquivo", "move file"]):
                quotes = re.findall(r'\"([^\"]+)\"', task or "")
                if len(quotes) >= 2:
                    return json.dumps({
                        "thought": "Modo offline: movendo arquivo.",
                        "action": "move_file",
                        "parameters": {"src": quotes[0], "dst": quotes[1]}
                    }, ensure_ascii=False)
                else:
                    return json.dumps({
                        "thought": "Faltam origem e destino para mover.",
                        "action": "answer",
                        "parameters": {"answer": "Use duas aspas: mover arquivo \"C:/a.txt\" \"C:/pasta/a.txt\"."}
                    }, ensure_ascii=False)

            # Renomear arquivo
            if any(k in t for k in ["renomear arquivo", "rename file"]):
                quotes = re.findall(r'\"([^\"]+)\"', task or "")
                if len(quotes) >= 2:
                    return json.dumps({
                        "thought": "Modo offline: renomeando arquivo.",
                        "action": "rename_file",
                        "parameters": {"path": quotes[0], "new_path": quotes[1]}
                    }, ensure_ascii=False)
                else:
                    return json.dumps({
                        "thought": "Faltam caminho antigo e novo para renomear.",
                        "action": "answer",
                        "parameters": {"answer": "Use duas aspas: renomear arquivo \"C:/a.txt\" \"C:/b.txt\"."}
                    }, ensure_ascii=False)

            # Anexar conteúdo
            if any(k in t for k in ["anexar", "append", "adicionar ao arquivo"]):
                quotes = re.findall(r'\"([^\"]+)\"', task or "")
                if quotes:
                    path = quotes[0]
                    content = quotes[1] if len(quotes) > 1 else ""
                    return json.dumps({
                        "thought": "Modo offline: anexando conteúdo ao arquivo.",
                        "action": "append_file",
                        "parameters": {"path": path, "content": content}
                    }, ensure_ascii=False)
                else:
                    return json.dumps({
                        "thought": "Sem caminho para anexar conteúdo.",
                        "action": "answer",
                        "parameters": {"answer": "Informe caminho e conteúdo entre aspas: anexar \"C:/log.txt\" \"linha\"."}
                    }, ensure_ascii=False)

            # Hash de arquivo
            if any(k in t for k in ["hash", "checksum", "sha256"]):
                m = re.search(r'\"([^\"]+)\"', task or "")
                algo = "sha256"
                if "md5" in t:
                    algo = "md5"
                elif "sha1" in t:
                    algo = "sha1"
                if m:
                    return json.dumps({
                        "thought": "Modo offline: calculando hash do arquivo.",
                        "action": "file_hash",
                        "parameters": {"path": m.group(1), "algorithm": algo}
                    }, ensure_ascii=False)
                else:
                    return json.dumps({
                        "thought": "Sem arquivo para calcular hash.",
                        "action": "answer",
                        "parameters": {"answer": "Informe o caminho do arquivo entre aspas."}
                    }, ensure_ascii=False)

            # Criar ZIP
            if any(k in t for k in ["zip", "zipar", "criar zip"]):
                quotes = re.findall(r'\"([^\"]+)\"', task or "")
                if len(quotes) >= 2:
                    return json.dumps({
                        "thought": "Modo offline: criando arquivo ZIP.",
                        "action": "zip_create",
                        "parameters": {"source": quotes[0], "zip_path": quotes[1]}
                    }, ensure_ascii=False)
                else:
                    return json.dumps({
                        "thought": "Faltam origem e destino para ZIP.",
                        "action": "answer",
                        "parameters": {"answer": "Use duas aspas: criar zip \"C:/pasta\" \"C:/backup.zip\"."}
                    }, ensure_ascii=False)

            # Extrair ZIP
            if any(k in t for k in ["extrair zip", "unzip", "descompactar"]):
                quotes = re.findall(r'\"([^\"]+)\"', task or "")
                if len(quotes) >= 2:
                    return json.dumps({
                        "thought": "Modo offline: extraindo arquivo ZIP.",
                        "action": "zip_extract",
                        "parameters": {"zip_path": quotes[0], "dest": quotes[1]}
                    }, ensure_ascii=False)
                else:
                    return json.dumps({
                        "thought": "Faltam ZIP e destino.",
                        "action": "answer",
                        "parameters": {"answer": "Use duas aspas: extrair zip \"C:/backup.zip\" \"C:/restaurado\"."}
                    }, ensure_ascii=False)

            # Download de arquivo
            if any(k in t for k in ["baixar", "download"]):
                url_match = re.search(r"https?://\S+", task or "")
                quotes = re.findall(r'\"([^\"]+)\"', task or "")
                dest = quotes[-1] if quotes else ""
                if url_match and dest:
                    return json.dumps({
                        "thought": "Modo offline: realizando download.",
                        "action": "download_file",
                        "parameters": {"url": url_match.group(0), "dest": dest}
                    }, ensure_ascii=False)
                else:
                    return json.dumps({
                        "thought": "Faltam URL e destino para download.",
                        "action": "answer",
                        "parameters": {"answer": "Ex.: baixar https://site/arquivo.zip \"C:/temp/arquivo.zip\"."}
                    }, ensure_ascii=False)

            # Processos
            if any(k in t for k in ["listar processos", "processos", "process list"]):
                return json.dumps({
                    "thought": "Modo offline: listando processos em execução.",
                    "action": "list_processes",
                    "parameters": {"top_n": 20}
                }, ensure_ascii=False)

            # Encerrar processo
            if any(k in t for k in ["encerrar processo", "matar processo", "kill process", "terminar processo"]):
                pid_match = re.search(r"pid\s*(\d+)", t)
                quotes = re.findall(r'\"([^\"]+)\"', task or "")
                if pid_match:
                    return json.dumps({
                        "thought": "Modo offline: solicitando encerramento por PID.",
                        "action": "kill_process",
                        "parameters": {"pid": int(pid_match.group(1))}
                    }, ensure_ascii=False)
                elif quotes:
                    return json.dumps({
                        "thought": "Modo offline: solicitando encerramento por nome.",
                        "action": "kill_process",
                        "parameters": {"name": quotes[0]}
                    }, ensure_ascii=False)
                else:
                    return json.dumps({
                        "thought": "Sem PID ou nome para encerrar.",
                        "action": "answer",
                        "parameters": {"answer": "Indique PID (ex.: pid 1234) ou nome entre aspas."}
                    }, ensure_ascii=False)

            # Serviços (Windows)
            if any(k in t for k in ["listar serviços", "servicos", "list services"]):
                filt = ""
                quotes = re.findall(r'\"([^\"]+)\"', task or "")
                if quotes:
                    filt = quotes[0]
                return json.dumps({
                    "thought": "Modo offline: listando serviços.",
                    "action": "list_services",
                    "parameters": {"filter": filt}
                }, ensure_ascii=False)

            if any(k in t for k in ["iniciar serviço", "start service"]):
                quotes = re.findall(r'\"([^\"]+)\"', task or "")
                if quotes:
                    return json.dumps({
                        "thought": "Modo offline: iniciando serviço.",
                        "action": "start_service",
                        "parameters": {"name": quotes[0]}
                    }, ensure_ascii=False)

            if any(k in t for k in ["parar serviço", "stop service"]):
                quotes = re.findall(r'\"([^\"]+)\"', task or "")
                if quotes:
                    return json.dumps({
                        "thought": "Modo offline: parando serviço.",
                        "action": "stop_service",
                        "parameters": {"name": quotes[0]}
                    }, ensure_ascii=False)

            # Tarefas agendadas (Windows)
            if any(k in t for k in ["tarefas agendadas", "scheduled tasks", "listar tarefas"]):
                return json.dumps({
                    "thought": "Modo offline: listando tarefas agendadas.",
                    "action": "list_scheduled_tasks",
                    "parameters": {}
                }, ensure_ascii=False)

            # Rede
            if any(k in t for k in ["conexões de rede", "network connections", "listar conexões"]):
                return json.dumps({
                    "thought": "Modo offline: listando conexões de rede.",
                    "action": "list_network_connections",
                    "parameters": {}
                }, ensure_ascii=False)

            if any(k in t for k in ["portas abertas", "open ports", "escuta"]):
                return json.dumps({
                    "thought": "Modo offline: mostrando portas em escuta.",
                    "action": "open_ports",
                    "parameters": {}
                }, ensure_ascii=False)

            if any(k in t for k in ["firewall", "estado do firewall"]):
                return json.dumps({
                    "thought": "Modo offline: consultando estado do firewall.",
                    "action": "firewall_state",
                    "parameters": {}
                }, ensure_ascii=False)

            if any(k in t for k in ["ping", "teste de latência"]):
                quotes = re.findall(r'\"([^\"]+)\"', task or "")
                host = quotes[0] if quotes else (re.search(r"\b([\w\.-]+\.[\w\.-]+|\d+\.\d+\.\d+\.\d+)\b", t) or [None])[0]
                count_match = re.search(r"\b(\d+)\s*vezes|\b(\d+)\b", t)
                count = int(count_match.group(1) or count_match.group(2)) if count_match else 4
                if host:
                    return json.dumps({
                        "thought": "Modo offline: executando ping.",
                        "action": "ping_host",
                        "parameters": {"host": host, "count": count}
                    }, ensure_ascii=False)
                else:
                    return json.dumps({
                        "thought": "Sem host para ping.",
                        "action": "answer",
                        "parameters": {"answer": "Informe um host/IP para ping (ex.: \"8.8.8.8\")."}
                    }, ensure_ascii=False)

            if any(k in t for k in ["traceroute", "tracert", "rota"]):
                quotes = re.findall(r'\"([^\"]+)\"', task or "")
                host = quotes[0] if quotes else (re.search(r"\b([\w\.-]+\.[\w\.-]+|\d+\.\d+\.\d+\.\d+)\b", t) or [None])[0]
                if host:
                    return json.dumps({
                        "thought": "Modo offline: executando traceroute.",
                        "action": "traceroute_host",
                        "parameters": {"host": host}
                    }, ensure_ascii=False)
                else:
                    return json.dumps({
                        "thought": "Sem host para traceroute.",
                        "action": "answer",
                        "parameters": {"answer": "Informe um host/IP para traceroute (ex.: \"8.8.8.8\")."}
                    }, ensure_ascii=False)

            # Variáveis de ambiente
            if any(k in t for k in ["obter variável", "get env", "ler variável de ambiente"]):
                quotes = re.findall(r'\"([^\"]+)\"', task or "")
                if quotes:
                    return json.dumps({
                        "thought": "Modo offline: lendo variável de ambiente.",
                        "action": "get_env",
                        "parameters": {"name": quotes[0]}
                    }, ensure_ascii=False)

            if any(k in t for k in ["definir variável", "set env", "exportar variável"]):
                quotes = re.findall(r'\"([^\"]+)\"', task or "")
                if len(quotes) >= 2:
                    return json.dumps({
                        "thought": "Modo offline: definindo variável de ambiente.",
                        "action": "set_env",
                        "parameters": {"name": quotes[0], "value": quotes[1]}
                    }, ensure_ascii=False)

            # Registro do Windows
            if any(k in t for k in ["ler registro", "read registry", "consultar registro"]):
                quotes = re.findall(r'\"([^\"]+)\"', task or "")
                if quotes:
                    return json.dumps({
                        "thought": "Modo offline: lendo chave do registro.",
                        "action": "read_registry",
                        "parameters": {"path": quotes[0]}
                    }, ensure_ascii=False)

            if any(k in t for k in ["escrever registro", "write registry", "definir chave"]):
                quotes = re.findall(r'\"([^\"]+)\"', task or "")
                if len(quotes) >= 3:
                    return json.dumps({
                        "thought": "Modo offline: escrevendo valor no registro.",
                        "action": "write_registry",
                        "parameters": {"path": quotes[0], "name": quotes[1], "value": quotes[2], "type": "String"}
                    }, ensure_ascii=False)

            # Regex
            if any(k in t for k in ["regex", "expressão regular", "expressao regular"]):
                quotes = re.findall(r'\"([^\"]+)\"', task or "")
                ext = ""
                mext = re.search(r"\.\w+", t)
                if mext:
                    ext = mext.group(0)
                if quotes:
                    return json.dumps({
                        "thought": "Modo offline: buscando por regex.",
                        "action": "search_regex",
                        "parameters": {"pattern": quotes[0], "extension": ext}
                    }, ensure_ascii=False)

            # Analisar sistema e telemetria
            if any(k in t for k in ["analisar sistema", "auditoria", "telemetria", "analyze system", "system audit"]):
                return json.dumps({
                    "thought": "Modo offline: coletando análise detalhada do sistema.",
                    "action": "analyze_system",
                    "parameters": {}
                }, ensure_ascii=False)

            # Listar arquivos
            if any(k in t for k in ["listar", "arquivos", "dir", "ls", "listar arquivos"]):
                cmd = "dir" if os.name == "nt" else "ls -la"
                return json.dumps({
                    "thought": "Modo offline: vou listar arquivos usando comando do sistema.",
                    "action": "execute_command",
                    "parameters": {"command": cmd}
                }, ensure_ascii=False)

            # Buscar conhecimento local
            if any(k in t for k in ["comando", "windows", "conhecimento", "ajuda", "manual"]):
                return json.dumps({
                    "thought": "Modo offline: vou consultar base de conhecimento local.",
                    "action": "knowledge_search",
                    "parameters": {"query": task, "top_k": 5}
                }, ensure_ascii=False)

            # Buscar URL
            url_match = re.search(r"https?://\S+", task or "")
            if any(k in t for k in ["web", "url", "http", "https"]) or url_match:
                url = url_match.group(0) if url_match else ""
                if url:
                    return json.dumps({
                        "thought": "Modo offline: vou buscar conteúdo da URL informada.",
                        "action": "fetch_url",
                        "parameters": {"url": url}
                    }, ensure_ascii=False)
                else:
                    return json.dumps({
                        "thought": "Sem URL específica disponível para buscar.",
                        "action": "answer",
                        "parameters": {"answer": "Forneça uma URL para que eu possa buscar o conteúdo."}
                    }, ensure_ascii=False)

            # Padrão
            return json.dumps({
                "thought": "LLM indisponível. Operando em modo offline com ferramentas locais.",
                "action": "answer",
                "parameters": {"answer": "Posso executar comandos locais (ex.: systeminfo, Get-CimInstance) e buscar conhecimento. Ative o Ollama para respostas de IA."}
            }, ensure_ascii=False)
        except Exception:
            return json.dumps({
                "thought": "Falha na heurística offline.",
                "action": "answer",
                "parameters": {"answer": "Ocorreu um erro ao decidir ação em modo offline."}
            }, ensure_ascii=False)

    def _offline_estimate_year(self, outputs: list[str]) -> str:
        try:
            text = "\n".join(outputs)
            # Tenta extrair ano especificamente da linha ReleaseDate
            for line in text.splitlines():
                if "releasedate" in line.lower():
                    m = re.search(r"(\d{4})", line)
                    if m:
                        return m.group(1)
            # Fallback: procurar por padrão de data completo em qualquer lugar
            m2 = re.search(r"(\d{4})[/\-]\d{1,2}[/\-]\d{1,2}", text)
            if m2:
                return m2.group(1)
            # Fallback genérico: primeira ocorrência de ano razoável (>= 2000)
            m3 = re.findall(r"\b(20\d{2}|19\d{2})\b", text)
            if m3:
                return m3[0]
        except Exception:
            pass
        return "N/D"

    def _offline_summarize_hardware(self, outputs: list[str], year_hint: str) -> str:
        try:
            # Resumo simples agregando seções capturadas
            sections = [
                "System Info", "CPU", "Memória Física", "Discos", "Placa-mãe", "GPU", "BIOS"
            ]
            joined = "\n\n".join(outputs) if outputs else "Sem saídas capturadas."
            return (
                "Características detalhadas da máquina (coletadas offline):\n" +
                joined +
                "\n\nAno de fabricação (estimado): " + str(year_hint)
            )
        except Exception:
            return "Falha ao gerar resumo das características da máquina."
    def _is_ollama_running(self):
        try:
            base = self._ollama_base_url()
            r = requests.get(f"{base}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def _start_ollama_server(self, max_wait=15):
        """Inicia o servidor Ollama e aguarda até estar pronto (com retry inteligente)."""
        try:
            if self._is_ollama_running():
                return True
            
            # Verifica se ollama CLI está disponível
            if not self._ollama_cli_available():
                return False
            
            # Inicia servidor em processo separado
            CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            subprocess.Popen(
                ["ollama", "serve"],
                creationflags=CREATE_NEW_CONSOLE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # Retry com backoff exponencial
            wait_times = [1, 2, 3, 4, 5]  # Total: 15s
            for wait in wait_times:
                time.sleep(wait)
                if self._is_ollama_running():
                    return True
            
            return False
        except Exception:
            return False

    def _ensure_model_available(self, model_name):
        # Verifica lista de modelos instalados; se não houver, tenta 'ollama pull'
        # Ignora se for um modelo de teste (mock)
        if "mock" in model_name.lower():
            return

        try:
            canonical = self._canonical_model_name(model_name)
            base = self._ollama_base_url()
            r = requests.get(f"{base}/api/tags", timeout=4)
            if r.status_code == 200:
                data = r.json()
                tags = [m.get("name") for m in data.get("models", [])]
                bases = { (n or "").split(":")[0] for n in tags }
                # Garante exatamente ':latest' do modelo desejado
                if canonical not in tags:
                    subprocess.run(["ollama", "pull", canonical], check=False)
                # Também cobre o caso em que somente a base existe
                elif model_name.split(":")[0] not in bases:
                    subprocess.run(["ollama", "pull", canonical], check=False)
        except Exception:
            pass
