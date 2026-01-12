# By-CRR Soluções em Tecnologia AI - Assistente de IA com Ollama

Um assistente de inteligência artificial autônomo que utiliza Ollama para processamento local de linguagem natural. Por padrão, o aplicativo utiliza o modelo `phi4` (quando disponível no Ollama). Caso não esteja disponível, faz fallback para `phi3`. Sistema foi experimentado usando dados publicos de empresa de saneamento e do SUS para explorar capacidade de respostas e possivel aplicação em empresas do ramo de saneamento e saude pública

## 🚀 Instalação

### Método 1: Instalador Gráfico (Recomendado)
1. Execute `install.bat`
2. O instalador com interface gráfica será aberto
3. Clique em "INICIAR INSTALAÇÃO" e aguarde o progresso
4. O atalho será criado automaticamente na área de trabalho com ícone (hexágono azul)

### Método 2: Instalação Manual
1. Certifique-se de ter Python 3.8+ instalado
2. Instale as dependências:
   ```bash
   pip install requests
   ```
3. Execute o sistema:
   ```bash
   python warpclone_gui.py
   ```

### Método 3: Executável
1. Execute `build_executable.py`
2. O executável `ByCRR_AI.exe` será criado na pasta `dist/`
3. Crie um atalho manualmente para o executável

## 📖 Como Usar

### Modo Interativo
```bash
python warpclone.py
```

Depois digite suas tarefas:
- "Liste todos os arquivos python nesta pasta"
- "Crie um script que imprime 'Hello World'"
- "Analise o conteúdo do arquivo config.json"
- "Execute o comando 'ls -la' e mostre os resultados"

### Modo Comando Direto
```bash
python warpclone.py "crie um arquivo teste.txt com o conteúdo 'Olá Mundo'"
```

## 🎯 Capacidades

O By-CRR Soluções em Tecnologia AI pode:

### Executar Comandos do Sistema
- Qualquer comando bash/shell
- Com timeout de segurança (30s)

### Manipular Arquivos
- Ler arquivos existentes
- Criar novos arquivos
- Modificar arquivos existentes

### Navegar Diretórios
- Listar conteúdo de pastas
- Obter informações sobre arquivos

### Pesquisas Avançadas
- **Pesquisa no sistema de arquivos**: Busca por padrões (ex: `*.py`, `*.txt`)
- **Pesquisa de conteúdo**: Busca texto dentro de arquivos
- **Pesquisa web**: Busca informações na internet (simulado)
- **Análise de sistema**: Analisa o ambiente e recursos

### Sistema de Aprendizado
- **Memória persistente**: Armazena tarefas e resultados
- **Padrões de aprendizado**: Identifica padrões de sucesso
- **Histórico de comandos**: Registra todas as ações
- **Melhoria contínua**: Aprende com experiências passadas

### Raciocínio Autônomo
- Decide quais ações tomar
- Executa múltiplas etapas
- Aprende com resultados anteriores
- Melhora o desempenho com o tempo

## 🔧 Exemplos de Tarefas

### Criar e executar um script
```bash
python warpclone.py "crie um script Python que calcula fatorial e execute para n=5"
```

### Análise de arquivos
```bash
python warpclone.py "leia todos os arquivos .py nesta pasta e me dê um resumo"
```

### Automatização
```bash
python warpclone.py "organize os arquivos desta pasta por extensão"
```

### Busca de informações
```bash
python warpclone.py "encontre todos os arquivos modificados hoje"
```

### Pesquisas avançadas
```bash
# Pesquisar arquivos por padrão
python warpclone.py "pesquise todos os arquivos Python nesta pasta"

# Pesquisar conteúdo dentro de arquivos
python warpclone.py "pesquise por 'def main' nos arquivos Python"

# Análise de sistema
python warpclone.py "analise o sistema e me diga o que encontrou"

# Busca na web (simulada)
python warpclone.py "pesquise na web sobre Python decorators"
```

## ⚙️ Configuração Avançada

### Alterar o modelo LLM (phi4 por padrão)
- Edite `warpclone_config.json` e ajuste:
  - `llm_model`: nome do modelo Ollama (ex.: `phi4`, `phi3`, `llama3`)
  - `ollama_url`: normalmente `http://localhost:11434/api/chat`
  
Exemplo:
```json
{
  "llm_model": "phi4",
  "ollama_url": "http://localhost:11434/api/chat"
}
```

### Modo Offline e Autostart do Ollama
- Para operar sem LLM e evitar avisos, ative o modo offline:
```json
{
  "offline_mode": true,
  "ollama_autostart": false,
  "ollama_check_interval_sec": 30
}
```
- `offline_mode`: desativa tentativas de conexão ao Ollama e usa heurísticas locais.
- `ollama_autostart`: se `true`, tenta iniciar `ollama serve` automaticamente quando estiver disponível.
- `ollama_check_interval_sec`: cache da verificação de disponibilidade para evitar checagens repetidas.

### Ajustar timeout de comandos
No método execute_command, modifique:
```python
timeout=30  # segundos
```

### Aumentar histórico de contexto
No método call_ollama, modifique:
```python
for msg in self.conversation_history[-5:]:  # Últimas 5 mensagens
```

## 🛡️ Segurança

⚠️ **IMPORTANTE**: Este sistema executa comandos com suas permissões. Use com cuidado!

Recomendações:
- Rode em ambiente isolado/sandbox
- Revise comandos antes de executar em produção
- Use usuário com permissões limitadas
- Monitore as ações executadas

## 🐛 Troubleshooting

### "Erro ao comunicar com Ollama"
- Verifique se o Ollama está rodando: `ollama serve`
- Confirme a porta: deve ser 11434

### "Modelo não encontrado"
```bash
ollama pull phi4
# se falhar, tente
ollama pull phi3
```

### Respostas lentas
- Phi-3 é um modelo pequeno e rápido
- Se ainda estiver lento, considere usar GPU
- Verifique recursos do sistema

### O agente não executa ações
- O modelo pode não estar seguindo o formato JSON
- Tente reformular a tarefa de forma mais específica
- Considere usar um modelo maior (llama2, mistral)

## 🔄 Melhorias Futuras
- [x] Interface web
- [x] Histórico persistente
- [x] Integração com APIs externas
- [x] Sistema de plugins
- [ ] Múltiplos agentes cooperativos
- [x] Memória de longo prazo
- [ ] Integração com banco de dados
- [ ] Interface gráfica
- [ ] Suporte para múltiplos modelos de IA
- [ ] Sistema de notificações

## 📝 Estrutura do Projeto
```
BY-CRR AI/
├── warpclone.py              # Script principal
├── requirements.txt          # Dependências
├── README.md                 # Este arquivo
├── warpclone_memory/         # Diretório de memória
│   ├── memory.json          # Memória persistente
│   └── learning_patterns.json # Padrões de aprendizado
└── warpclone_logs/          # Diretório de logs
    └── command_history.json # Histórico de comandos
```

## 💡 Dicas de Uso
- **Seja específico**: "Crie um servidor HTTP na porta 8000" é melhor que "crie um servidor"
- **Tarefas complexas**: O agente pode executar até 10 iterações. Divida tarefas muito complexas.
- **Contexto**: O agente lembra das últimas 5 interações para manter contexto.
- **Erros**: Se algo falhar, o agente tentará corrigir automaticamente.
- **Pesquisas**: Use termos claros para pesquisas de arquivos e conteúdo
- **Aprendizado**: O sistema melhora com o uso - quanto mais você usar, mais inteligente ele fica
- **Memória**: As memórias são salvas automaticamente e persistem entre sessões

## 🗑️ Desinstalação

### Método 1: Desinstalador Gráfico (Recomendado)
1. Execute `uninstall.bat`
2. O desinstalador com interface gráfica será aberto
3. Escolha se deseja remover também seus dados pessoais
4. Clique em "DESINSTALAR"

### Método 2: Desinstalação Manual
1. Remova o atalho da área de trabalho: `By-CRR Soluções em Tecnologia AI.lnk`
2. Delete a pasta do projeto
3. Remova as pastas `warpclone_memory` e `warpclone_logs` (opcional)
4. Delete o arquivo `warpclone_config.json` (opcional)

## 📄 Licença
Livre para uso pessoal e educacional.
