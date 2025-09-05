#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Labirinto dos Processos — Fantasmas SSE v3 (versão em Português)
- UI em tempo real via Server-Sent Events (SSE)
- Fantasmas (Pac-Man) com animação suave + anel de progresso + legenda de atividade
- Labirinto GERADO (DFS): conectado; saída é a célula mais distante do início; 2 pontos de controle e 1 gargalo obrigatórios
- Processos reais (multiprocessing) com PAUSA/CONTINUA/KILL multiplataforma (cooperação via flags)
- Sincronização: semáforo no 'G'
"""

import os, signal, time, random, json, threading, queue, sys
from collections import deque
from multiprocessing import Process, Queue as FilaMP, Manager, Semaphore
from flask import Flask, Response, request

# ===================== Gerador de Labirinto (robusto) =====================

def _vizinhos_2(linha, coluna, linhas, colunas):
    """Gera vizinhos pulando 2 células (para o backtracker em grade ímpar)."""
    for dl, dc in ((2,0),(-2,0),(0,2),(0,-2)):
        nl, nc = linha + dl, coluna + dc
        if 1 <= nl < linhas-1 and 1 <= nc < colunas-1:
            # retorna também o "passo intermediário" (dl//2, dc//2)
            yield nl, nc, dl//2, dc//2

def limitar(valor, minimo, maximo):
    """Limita valor ao intervalo [minimo, maximo]."""
    return max(minimo, min(maximo, valor))

def gerar_labirinto(linhas=23, colunas=43, pontos_de_controle=2):
    """Gera um labirinto perfeito e marca S/C/G no caminho principal com índices protegidos.

    - linhas/colunas são forçadas a ímpares (melhora o algoritmo em grade quadriculada)
    - Início fixo em (1,1)
    - Saída escolhida como a célula mais distante do início (via BFS)
    - Marca C (checkpoints), G (gargalo) e S (saída) no caminho principal
    - Retorna: grid, inicio, saida, lista_checkpoints, gargalo, celulas_livres
    """
    # garantir dimensões ímpares
    linhas = linhas if linhas % 2 == 1 else linhas + 1
    colunas = colunas if colunas % 2 == 1 else colunas + 1

    grid = [['#'] * colunas for _ in range(linhas)]
    inicio = (1, 1)
    grid[1][1] = '.'

    # DFS iterativo (backtracker)
    pilha = [inicio]
    rng = random.Random(int(time.time()))
    while pilha:
        l, c = pilha[-1]
        escolhas = [
            (nl, nc, pl, pc)
            for (nl, nc, pl, pc) in _vizinhos_2(l, c, linhas, colunas)
            if grid[nl][nc] == '#'
        ]
        if not escolhas:
            pilha.pop(); continue
        nl, nc, pl, pc = rng.choice(escolhas)
        grid[l + pl][c + pc] = '.'
        grid[nl][nc] = '.'
        pilha.append((nl, nc))

    # BFS a partir do início para achar distâncias/rotas
    def bfs(origem):
        from collections import deque as dq
        fila = dq([origem])
        dist = {origem: 0}
        anterior = {origem: None}
        while fila:
            ll, cc = fila.popleft()
            for dl, dc in ((1,0),(-1,0),(0,1),(0,-1)):
                nl, nc = ll + dl, cc + dc
                if 0 <= nl < linhas and 0 <= nc < colunas and grid[nl][nc] == '.' and (nl, nc) not in dist:
                    dist[(nl, nc)] = dist[(ll, cc)] + 1
                    anterior[(nl, nc)] = (ll, cc)
                    fila.append((nl, nc))
        return dist, anterior

    dist, anterior = bfs(inicio)
    # saída é a célula mais distante do início
    saida = max(dist, key=dist.get)

    # caminho principal (inicio -> saida)
    caminho = []
    atual = saida
    while atual is not None:
        caminho.append(atual)
        atual = anterior.get(atual)
    caminho.reverse()
    L = len(caminho)

    # Caso patológico: caminho muito curto. Aumenta grade e tenta novamente.
    if L < 3:
        return gerar_labirinto(linhas + 2, colunas + 2, pontos_de_controle)

    # coloca pontos de controle (C) em frações do caminho
    pontos_de_controle = max(1, pontos_de_controle)
    lista_C = []
    usados = set()
    for k in range(1, pontos_de_controle + 1):
        idx = int(round(L * k / (pontos_de_controle + 1)))
        idx = limitar(idx, 1, L - 2)  # evita extremos
        while idx in usados and (1 <= idx <= L - 2):
            idx = limitar(idx + 1, 1, L - 2)
            if idx in usados:
                idx = limitar(idx - 2, 1, L - 2)
        usados.add(idx)
        lista_C.append(caminho[idx])

    # gargalo (G) próximo do meio do caminho, sem colidir com C ou extremos
    meio = limitar(L // 2, 1, L - 2)
    desloc = 0
    while caminho[meio] in lista_C or meio in (0, L - 1):
        desloc += 1
        meio = limitar((L // 2) + ((-1) ** desloc) * desloc, 1, L - 2)
        if desloc > L:
            break  # redundância defensiva
    celula_gargalo = caminho[meio]

    # limpa quaisquer marcas anteriores e grava C/G/S
    for ll in range(linhas):
        for cc in range(colunas):
            if grid[ll][cc] in ('C', 'S', 'G'):
                grid[ll][cc] = '.'
    for (ll, cc) in lista_C:
        grid[ll][cc] = 'C'
    gl, gc = celula_gargalo
    grid[gl][gc] = 'G'
    sl, sc = saida
    grid[sl][sc] = 'S'

    # células caminháveis
    livres = [
        (l, c)
        for l in range(linhas)
        for c in range(colunas)
        if grid[l][c] in ('.', 'C', 'G', 'S')
    ]

    return grid, inicio, saida, lista_C, celula_gargalo, livres

# --- gera um labirinto válido na inicialização
LABIRINTO, INICIO, SAIDA, PONTOS, GARGALO, LIVRES = gerar_labirinto(linhas=23, colunas=43, pontos_de_controle=2)
LINHAS, COLUNAS = len(LABIRINTO), len(LABIRINTO[0])

# Utilitários de acesso ao grid

def celula(l, c):
    return LABIRINTO[l][c]

def caminhavel(l, c):
    return celula(l, c) in ".CGS"

def dentro(l, c):
    return 0 <= l < LINHAS and 0 <= c < COLUNAS

def vizinhos(l, c):
    for dl, dc in ((1,0),(-1,0),(0,1),(0,-1)):
        nl, nc = l + dl, c + dc
        if dentro(nl, nc):
            yield nl, nc

# ===================== BFS próximo passo =====================
from collections import deque as dq

def proximo_passo_bfs(origem, alvos: set):
    """Retorna o próximo passo (uma célula adjacente) no caminho curto até algum alvo."""
    ol, oc = origem
    if origem in alvos:
        return origem
    fila = dq([origem])
    anterior = {origem: None}
    encontrado = None
    while fila:
        l, c = fila.popleft()
        if (l, c) in alvos:
            encontrado = (l, c)
            break
        for nl, nc in vizinhos(l, c):
            if not caminhavel(nl, nc):
                continue
            if (nl, nc) in anterior:
                continue
            anterior[(nl, nc)] = (l, c)
            fila.append((nl, nc))
    if not encontrado:
        return None
    atual = encontrado
    while anterior[atual] and anterior[atual] != origem:
        atual = anterior[atual]
    return atual

# ===================== Tarefas (CPU/IO) =====================

def tarefa_primos(limite=170000):
    """Conta primos até 'limite' usando Crivo de Eratóstenes (versão compacta)."""
    if limite < 2:
        return 0
    crivo = bytearray(b"\x01") * (limite + 1)
    crivo[0] = crivo[1] = 0
    p = 2
    while p * p <= limite:
        if crivo[p]:
            inicio = p * p
            passo = p
            crivo[inicio:limite + 1:passo] = b"\x00" * (((limite - inicio) // passo) + 1)
        p += 1
    return sum(crivo)

def tarefa_fibo(n=39):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a

def tarefa_io(segundos=1.1):
    time.sleep(segundos)
    return segundos

# ===================== Agente (processo) =====================

# Detecta se há suporte a sinais POSIX (Linux/macOS). No Windows, usaremos flags cooperativas.
POSIX_SIGNALS = (os.name != 'nt') and hasattr(signal, 'SIGSTOP') and hasattr(signal, 'SIGCONT')

def agente(nome, pos_inicial, fila_saida: FilaMP, gargalo: Semaphore, pausa_flags, kill_flags, passo_ms=170):
    """Processo que caminha no labirinto, executa tarefas nos 'C' e finaliza ao chegar na 'S'.
    Suporta pausa/continuação/encerramento via flags compartilhadas (multiplataforma).
    """
    pid = os.getpid()
    random.seed(pid ^ int(time.time()))
    l, c = pos_inicial
    concluido, total = 0, 3
    rodando = True

    def ao_terminar(_sig, _frm):
        nonlocal rodando
        rodando = False

    # Mesmo no Windows, SIGTERM existe; usamos para término cooperativo/forçado
    signal.signal(signal.SIGTERM, ao_terminar)

    def emitir(tipo, atividade):
        """Enfileira um evento para a UI/SSE com o estado atual do agente."""
        try:
            fila_saida.put({
                "tipo": tipo,
                "pid": pid,
                "nome": nome,
                "posicao": [l, c],
                "feito": concluido,
                "total": total,
                "atividade": atividade,
                "pausado": bool(pausa_flags.get(pid, False))
            }, block=False)
        except Exception:
            pass

    tarefas = [
        ("primos 170k", lambda: tarefa_primos(170000)),
        ("fibonacci 39", lambda: tarefa_fibo(39)),
        ("io 1.1s", lambda: tarefa_io(1.1))
    ]

    emitir("nascimento", "iniciando")

    while rodando:
        # Encerramento cooperativo
        if kill_flags.get(pid, False):
            emitir("fim", "encerrado pelo gerenciador")
            break

        # Pausa cooperativa
        if pausa_flags.get(pid, False):
            emitir("estado", "pausado")
            time.sleep(0.25)
            continue

        alvos = set(PONTOS) if concluido < total else {SAIDA}
        passo = proximo_passo_bfs((l, c), alvos)
        if not passo:
            # Sem caminho encontrado no momento (raro). Move aleatório válido.
            opcoes = [(nl, nc) for nl, nc in vizinhos(l, c) if caminhavel(nl, nc)]
            if not opcoes:
                time.sleep(passo_ms / 1000)
                continue
            nl, nc = random.choice(opcoes)
        else:
            nl, nc = passo

        # Controle de concorrência no gargalo (G)
        if celula(nl, nc) == 'G':
            emitir("estado", "aguardando semáforo")
            sem_obtido = False
            while not sem_obtido:
                if kill_flags.get(pid, False):
                    emitir("fim", "encerrado pelo gerenciador")
                    return
                if not pausa_flags.get(pid, False):
                    sem_obtido = gargalo.acquire(timeout=0.1)
                else:
                    emitir("estado", "pausado")
                    time.sleep(0.25)
            emitir("estado", "entrando no gargalo")
            time.sleep(passo_ms / 1000)
            gargalo.release()
            emitir("estado", "saindo do gargalo")

        l, c = nl, nc
        emitir("movimento", "caminhando")

        # Ao pisar em um C, executa a próxima tarefa
        if celula(l, c) == 'C' and concluido < total:
            nome_tarefa, func_tarefa = tarefas[concluido]
            emitir("estado", f"executando: {nome_tarefa}")
            t0 = time.time()
            try:
                _ = func_tarefa()
            except Exception as ex:
                emitir("estado", f"erro na tarefa: {ex}")
            dt = time.time() - t0
            concluido += 1
            emitir("estado", f"concluída ({nome_tarefa}) em {dt:.2f}s")

        # Ao chegar na saída (S) com todas as tarefas feitas, finaliza
        if (l, c) == SAIDA and concluido >= total:
            emitir("saida", "finalizado")
            break

        time.sleep(passo_ms / 1000)

    emitir("fim", "terminado")

# ===================== Gerenciador + SSE =====================

app = Flask(__name__)

# Estruturas compartilhadas entre processos/threads
_gerenciador = Manager()
compartilhado = _gerenciador.dict()   # pid -> último estado emitido
filhos = {}                           # pid -> (Processo, nome)
parados = set()                       # PIDs parados (visão do servidor)
buffer_logs = deque(maxlen=500)       # histórico de logs para a UI
fila_eventos = FilaMP(maxsize=10000)  # fila principal de eventos dos agentes
sem_gargalo = Semaphore(1)            # semáforo do gargalo 'G'

# NOVO: flags cooperativas multiplataforma
pausa_flags = _gerenciador.dict()     # pid -> bool
kill_flags  = _gerenciador.dict()     # pid -> bool

# pub/sub simples para SSE
_assinantes = set()
_assinantes_lock = threading.Lock()

def _difundir(obj: dict):
    """Entrega um objeto JSON a todos os assinantes SSE atuais."""
    dados = json.dumps(obj, ensure_ascii=False)
    with _assinantes_lock:
        mortos = []
        for q in list(_assinantes):
            try:
                q.put_nowait(dados)
            except queue.Full:
                mortos.append(q)
        for q in mortos:
            _assinantes.discard(q)

def _logar(linha: str):
    buffer_logs.appendleft(linha)
    _difundir({"tipo": "log", "linha": linha})

def bomba_eventos():
    """Thread que consome a fila_eventos e repassa para os assinantes SSE."""
    while True:
        ev = fila_eventos.get()
        pid = ev["pid"]
        compartilhado[pid] = ev

        # Loga somente eventos importantes
        if ev["tipo"] in ("nascimento", "estado", "saida", "fim"):
            _logar(
                f"{time.strftime('%H:%M:%S')} | {ev['nome']}:{pid} :: {ev['atividade']} | pos={tuple(ev['posicao'])} | {ev['feito']}/{ev['total']}"
            )

        # Entrega o evento para a UI
        _difundir({"tipo": "agente", "dados": ev})

        # Limpeza quando agente termina
        if ev["tipo"] in ("saida", "fim"):
            proc = filhos.get(pid, (None, None))[0]
            if proc is not None:
                proc.join(timeout=0.1)
            filhos.pop(pid, None)
            parados.discard(pid)
            pausa_flags.pop(pid, None)
            kill_flags.pop(pid, None)

def gerar(qtd=3):
    """Cria 'qtd' agentes em células livres, evitando S/C/G na largada."""
    evitar = {SAIDA, GARGALO, *PONTOS}
    candidatos = [pos for pos in LIVRES if pos not in evitar]
    random.shuffle(candidatos)
    for i in range(qtd):
        pos = candidatos[i % len(candidatos)]
        nome = f"Fantasma-{int(time.time()) % 10000}-{i + 1}"
        p = Process(target=agente, args=(nome, pos, fila_eventos, sem_gargalo, pausa_flags, kill_flags, 170), daemon=True)
        p.start()
        filhos[p.pid] = (p, nome)
        pausa_flags[p.pid] = False
        kill_flags[p.pid] = False
        _logar(f"{time.strftime('%H:%M:%S')} | gerenciador :: criado {nome}:{p.pid} em {pos}")

# ===================== Endpoints auxiliares =====================

def _parar_pid(pid: int):
    if pid not in filhos:
        return False
    if POSIX_SIGNALS:
        try:
            os.kill(pid, signal.SIGSTOP)
            return True
        except ProcessLookupError:
            return False
    else:
        pausa_flags[pid] = True
        return True

def _continuar_pid(pid: int):
    if pid not in filhos:
        return False
    if POSIX_SIGNALS:
        try:
            os.kill(pid, signal.SIGCONT)
            return True
        except ProcessLookupError:
            return False
    else:
        pausa_flags[pid] = False
        return True

def _matar_pid(pid: int):
    if pid not in filhos:
        return False
    kill_flags[pid] = True
    # Fallback imediato: tenta encerrar o processo objeto se disponível
    proc = filhos.get(pid, (None, None))[0]
    if proc is not None and proc.is_alive():
        try:
            proc.terminate()
        except Exception:
            pass
    if POSIX_SIGNALS:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    return True

# ===================== Endpoints SSE/HTTP =====================

@app.get("/eventos")
def sse_eventos():
    """Canal SSE principal. Envia estado inicial e atualizações em tempo real."""
    q = queue.Queue(maxsize=2048)
    with _assinantes_lock:
        _assinantes.add(q)

    # Estado inicial para novos assinantes
    q.put_nowait(json.dumps({
        "tipo": "ola",
        "linhas": LINHAS,
        "colunas": COLUNAS,
        "labirinto": ["".join(linha) for linha in LABIRINTO]
    }))
    q.put_nowait(json.dumps({
        "tipo": "instantaneo",
        "dados": list(compartilhado.values())
    }))
    q.put_nowait(json.dumps({
        "tipo": "logs",
        "dados": list(buffer_logs)
    }))

    def gerar_stream():
        try:
            while True:
                dados = q.get()
                yield f"data: {dados}\n\n"
        except GeneratorExit:
            with _assinantes_lock:
                _assinantes.discard(q)

    return Response(gerar_stream(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.post("/api/novo")
def api_novo():
    """Cria novos agentes. Parâmetro de query: quantidade (1..20)."""
    quantidade = max(1, min(20, int(request.args.get("quantidade", "1"))))
    gerar(quantidade)
    return ("", 204)

@app.post("/api/parar")
def api_parar():
    pid = int(request.args.get("pid", "0"))
    if pid in filhos and _parar_pid(pid):
        parados.add(pid)
        _logar(f"{time.strftime('%H:%M:%S')} | gerenciador :: PARAR -> {pid}")
    return ("", 204)

@app.post("/api/continuar")
def api_continuar():
    pid = int(request.args.get("pid", "0"))
    if pid in filhos and _continuar_pid(pid):
        parados.discard(pid)
        _logar(f"{time.strftime('%H:%M:%S')} | gerenciador :: CONTINUAR -> {pid}")
    return ("", 204)

@app.post("/api/matar")
def api_matar():
    pid = int(request.args.get("pid", "0"))
    if pid in filhos and _matar_pid(pid):
        parados.discard(pid)
        _logar(f"{time.strftime('%H:%M:%S')} | gerenciador :: MATAR -> {pid}")
    return ("", 204)

@app.post("/api/pararTodos")
def api_parar_todos():
    """Para todos os processos ativos."""
    for pid in list(filhos.keys()):
        _parar_pid(pid)
        parados.add(pid)
    _logar(f"{time.strftime('%H:%M:%S')} | gerenciador :: PARAR TODOS")
    return ("", 204)

@app.post("/api/continuarTodos")
def api_continuar_todos():
    """Continua todos os processos parados."""
    for pid in list(filhos.keys()):
        _continuar_pid(pid)
        parados.discard(pid)
    _logar(f"{time.strftime('%H:%M:%S')} | gerenciador :: CONTINUAR TODOS")
    return ("", 204)

@app.post("/api/matarTodos")
def api_matar_todos():
    """Encerra todos os processos."""
    for pid in list(filhos.keys()):
        _matar_pid(pid)
        parados.discard(pid)
    _logar(f"{time.strftime('%H:%M:%S')} | gerenciador :: MATAR TODOS")
    return ("", 204)

# ===================== UI (HTML/JS) =====================

INDEX_HTML = r"""<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Labirinto dos Processos — Fantasmas SSE v3</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<style>
:root {
  --bg-primary: #0f172a;
  --bg-secondary: #1e293b;
  --bg-tertiary: #334155;
  --accent-primary: #3b82f6;
  --accent-secondary: #8b5cf6;
  --accent-tertiary: #10b981;
  --text-primary: #f1f5f9;
  --text-secondary: #cbd5e1;
  --text-muted: #64748b;
  --border-color: #334155;
  --success: #10b981;
  --warning: #f59e0b;
  --danger: #ef4444;
  --info: #3b82f6;
  --grid-size: 22px;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  margin: 0;
  background: var(--bg-primary);
  color: var(--text-primary);
  font-family: 'Segoe UI', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
  display: grid;
  grid-template-columns: minmax(740px, 1fr) clamp(380px, 33vw, 540px);
  height: 100vh;
  overflow: hidden;
}

#esquerda { padding: 20px; display: flex; flex-direction: column; gap: 16px; overflow: auto; }
#direita { padding: 20px; border-left: 1px solid var(--border-color); overflow: auto; display: flex; flex-direction: column; gap: 20px; }

.header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.header h1 { font-size: 24px; font-weight: 600; color: var(--text-primary); }
.header-controls { display: none; gap: 8px; align-items: center; }

#quadro { position: relative; width: fit-content; border: 1px solid var(--border-color); background: var(--bg-secondary); border-radius: 8px; overflow: hidden; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2); }

canvas { image-rendering: pixelated; display: block; }

.controls { display: flex; gap: 12px; align-items: center; margin-bottom: 16px; }

.botao { padding: 10px 16px; border-radius: 8px; background: var(--bg-tertiary); border: 1px solid var(--border-color); color: var(--text-primary); cursor: pointer; font-weight: 500; display: flex; align-items: center; gap: 6px; transition: all 0.2s ease; }
.botao:hover { background: #475569; transform: translateY(-1px); }
.botao.primary { background: var(--accent-primary); border-color: var(--accent-primary); }
.botao.primary:hover { background: #2563eb; }
.botao.danger { background: var(--danger); border-color: var(--danger); }
.botao.danger:hover { background: #dc2626; }
.botao.success { background: var(--success); border-color: var(--success); }
.botao.success:hover { background: #059669; }
.botao.warning { background: var(--warning); border-color: var(--warning); }
.botao.warning:hover { background: #d97706; }
.botao.small { padding: 6px 10px; font-size: 12px; }

input[type="number"] { padding: 10px; background: var(--bg-secondary); border: 1px solid var(--border-color); color: var(--text-primary); border-radius: 8px; width: 80px; }

.card { background: var(--bg-secondary); border-radius: 12px; padding: 16px; border: 1px solid var(--border-color); box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1); }
.card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.card-title { font-size: 16px; font-weight: 600; color: var(--text-primary); }
.card-actions { display: flex; gap: 8px; }

table { width: 100%; border-collapse: collapse; font-size: 14px; margin-top: 8px; }
th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--border-color); }
th { font-weight: 500; color: var(--text-secondary); background: var(--bg-tertiary); position: sticky; top: 0; }
tr:hover { background: rgba(255, 255, 255, 0.05); }

.pilula { padding: 4px 10px; border-radius: 999px; background: var(--bg-tertiary); font-size: 12px; font-weight: 500; display: inline-block; text-align: center; min-width: 50px; }
.pilula.completed { background: var(--success); color: white; }
.pilula.partial { background: var(--warning); color: white; }
.pilula.pending { background: var(--bg-tertiary); color: var(--text-secondary); }

.legenda { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 12px; font-size: 13px; color: var(--text-secondary); }
.legenda-item { display: flex; align-items: center; gap: 6px; }
.legenda-cor { width: 16px; height: 16px; border-radius: 4px; display: inline-block; }
.cor-parede { background: #0f172a; }
.cor-caminho { background: #334155; }
.cor-checkpoint { background: #f59e0b; }
.cor-gargalo { background: #8b5cf6; }
.cor-saida { background: #10b981; }

#logs { font: 13px 'SF Mono', 'Monaco', 'Inconsolata', 'Roboto Mono', monospace; background: var(--bg-secondary); border: 1px solid var(--border-color); border-radius: 8px; padding: 12px; height: 240px; overflow: auto; display: flex; flex-direction: column-reverse; }
.log-entry { padding: 4px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.05); color: var(--text-secondary); }
.log-entry:last-child { border-bottom: none; }
.log-time { color: var(--accent-primary); margin-right: 8px; }
.log-pid { color: var(--accent-secondary); margin-right: 4px; font-weight: 500; }
.log-name { color: var(--text-primary); font-weight: 500; margin-right: 4px; }
.log-activity { color: var(--text-secondary); }

.stats { display: flex; gap: 16px; margin-bottom: 16px; }
.stat-card { background: var(--bg-secondary); border-radius: 8px; padding: 12px; border: 1px solid var(--border-color); flex: 1; text-align: center; }
.stat-value { font-size: 24px; font-weight: 600; color: var(--accent-primary); margin-bottom: 4px; }
.stat-label { font-size: 12px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; }

.etiqueta { font-size: 12px; padding: 4px 8px; border: 1px solid var(--border-color); background: var(--bg-tertiary); border-radius: 6px; color: var(--text-secondary); display: inline-flex; align-items: center; gap: 4px; }

.help-icon { color: var(--text-muted); cursor: help; }
.tooltip { position: relative; }
.tooltip-text { visibility: hidden; width: 200px; background: var(--bg-tertiary); color: var(--text-primary); text-align: center; border-radius: 6px; padding: 8px; position: absolute; z-index: 1; bottom: 125%; left: 50%; transform: translateX(-50%); opacity: 0; transition: opacity 0.3s; font-size: 12px; font-weight: normal; line-height: 1.4; }
.tooltip:hover .tooltip-text { visibility: visible; opacity: 1; }

.empty-state { text-align: center; padding: 40px 20px; color: var(--text-muted); }
.empty-state i { font-size: 32px; margin-bottom: 12px; opacity: 0.5; }
.empty-state p { margin-top: 8px; font-size: 14px; }

.progress-ring { transition: stroke-dashoffset 0.3s; }
@keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.5; } 100% { opacity: 1; } }
.pulsing { animation: pulse 2s infinite; }
</style>
</head>
<body>
<div id="esquerda">
  <div class="header">
    <h1>Labirinto dos Processos <span class="etiqueta"><i class="fas fa-ghost"></i> Fantasmas SSE v3</span></h1>
    <div class="header-controls">
      <div class="tooltip">
        <button class="botao" id="ajudaBtn"><i class="fas fa-question-circle"></i></button>
        <span class="tooltip-text">Cada fantasma é um processo que precisa completar 3 tarefas (nos pontos C) antes de sair pelo S. O G é um gargalo controlado por semáforo.</span>
      </div>
      <div class="tooltip">
        <button class="botao" id="refreshBtn"><i class="fas fa-sync-alt"></i> Atualizar Labirinto</button>
        <span class="tooltip-text">Gera um novo labirinto aleatório</span>
      </div>
    </div>
  </div>

  <div class="stats">
    <div class="stat-card">
      <div class="stat-value" id="total-processos">0</div>
      <div class="stat-label">Processos Ativos</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" id="processos-parados">0</div>
      <div class="stat-label">Processos Pausados</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" id="processos-finalizados">0</div>
      <div class="stat-label">Processos Finalizados</div>
    </div>
  </div>

  <div class="controls">
    <input id="qtd" type="number" min="1" max="20" value="3">
    <button class="botao primary" id="criarBtn"><i class="fas fa-plus-circle"></i> Criar Novos Processos</button>
    <button class="botao warning" id="pararTodosBtn"><i class="fas fa-pause"></i> Parar Todos</button>
    <button class="botao success" id="continuarTodosBtn"><i class="fas fa-play"></i> Continuar Todos</button>
    <button class="botao danger" id="matarTodosBtn"><i class="fas fa-skull"></i> Encerrar Todos</button>
  </div>

  <div id="quadro">
    <canvas id="labirinto"></canvas>
    <canvas id="atores" style="position:absolute;left:0;top:0;"></canvas>
    <canvas id="rotulos" style="position:absolute;left:0;top:0;pointer-events:none;"></canvas>
  </div>

  <div class="legenda">
    <div class="legenda-item"><span class="legenda-cor cor-parede"></span> Parede</div>
    <div class="legenda-item"><span class="legenda-cor cor-caminho"></span> Caminho</div>
    <div class="legenda-item"><span class="legenda-cor cor-checkpoint"></span> Ponto de Controle (C)</div>
    <div class="legenda-item"><span class="legenda-cor cor-gargalo"></span> Gargalo (G)</div>
    <div class="legenda-item"><span class="legenda-cor cor-saida"></span> Saída (S)</div>
  </div>
</div>

<div id="direita">
  <div class="card">
    <div class="card-header">
      <div class="card-title">Processos Ativos</div>
      <div class="card-actions">
        <button class="botao small" id="expandirBtn"><i class="fas fa-expand"></i></button>
        <button class="botao small" id="recolherBtn"><i class="fas fa-compress"></i></button>
      </div>
    </div>
    <div style="height: 400px; overflow: auto;">
      <table>
        <thead>
          <tr>
            <th>PID</th>
            <th>Nome</th>
            <th>Posição</th>
            <th>Progresso</th>
            <th>Atividade</th>
            <th>Ações</th>
          </tr>
        </thead>
        <tbody id="tcorpo">
          <tr>
            <td colspan="6" class="empty-state">
              <i class="fas fa-ghost"></i>
              <p>Nenhum processo ativo</p>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <div class="card-header">
      <div class="card-title">Log do Sistema</div>
      <div class="card-actions">
        <button class="botao small" id="limparLogsBtn"><i class="fas fa-trash"></i> Limpar</button>
      </div>
    </div>
    <div id="logs"></div>
  </div>
</div>

<script>
let linhas=0, colunas=0, grade=[];
const tam = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--grid-size') || 22);
const cnvLab = document.getElementById('labirinto');
const cnvAtores = document.getElementById('atores');
const cnvRotulos = document.getElementById('rotulos');
const ctxL = cnvLab.getContext('2d');
const ctxA = cnvAtores.getContext('2d');
const ctxR = cnvRotulos.getContext('2d');

// Estatísticas
let totalProcessos = 0;
let processosParados = 0;
let processosFinalizados = 0;

function atualizarEstatisticas() {
  document.getElementById('total-processos').textContent = totalProcessos;
  document.getElementById('processos-parados').textContent = processosParados;
  document.getElementById('processos-finalizados').textContent = processosFinalizados;
}

function desenharLabirinto(){
  cnvLab.width = colunas * tam;
  cnvLab.height = linhas * tam;
  cnvAtores.width = cnvLab.width;
  cnvAtores.height = cnvLab.height;
  cnvRotulos.width = cnvLab.width;
  cnvRotulos.height = cnvLab.height;

  for(let l = 0; l < linhas; l++){
    for(let c = 0; c < colunas; c++){
      const ch = grade[l][c];
      let cor = "#0f172a"; // padrão (parede)
      if(ch === '.') cor = "#334155"; // caminho
      else if(ch === 'C') cor = "#f59e0b"; // checkpoint
      else if(ch === 'G') cor = "#8b5cf6"; // gargalo
      else if(ch === 'S') cor = "#10b981"; // saída
      ctxL.fillStyle = cor;
      ctxL.fillRect(c * tam, l * tam, tam, tam);
      if(ch === '.') { // textura sutil
        ctxL.fillStyle = 'rgba(0, 0, 0, 0.08)';
        ctxL.fillRect(c * tam + 2, l * tam + 2, tam - 4, tam - 4);
      }
    }
  }
}

function centro(pos){ return [pos[1] * tam + tam / 2, pos[0] * tam + tam / 2]; }
function corHSL(pid){ const h = Math.abs(pid) % 360; return `hsl(${h}, 75%, 65%)`; }

const fantasmas = new Map(); // pid -> estado
const DUR = 170; // ms por passo (deve casar com o servidor)

function inserirOuAtualizar(msg){
  const pid = msg.pid;
  const g = fantasmas.get(pid) || {
    x: 0, y: 0, tx: 0, ty: 0, t0: performance.now(), t1: performance.now(), dir: [1, 0],
    nome: msg.nome || `Fantasma-${pid}`, cor: corHSL(pid), feito: 0, total: 3, atividade: "", estado: "ativo"
  };

  if(msg.posicao){
    const [nx, ny] = centro(msg.posicao);
    const dx = nx - (g.tx ?? nx);
    const dy = ny - (g.ty ?? ny);
    const len = Math.hypot(dx, dy) || 1;
    g.dir = [dx/len, dy/len];
    g.x = g.tx ?? nx;
    g.y = g.ty ?? ny;
    g.tx = nx;
    g.ty = ny;
    g.t0 = performance.now();
    g.t1 = g.t0 + DUR;
  }

  if(typeof msg.nome === 'string') g.nome = msg.nome;
  if(typeof msg.feito === 'number') g.feito = msg.feito;
  if(typeof msg.total === 'number') g.total = msg.total;
  if(typeof msg.atividade === 'string') g.atividade = msg.atividade;
  if(typeof msg.pausado === 'boolean') g.estado = msg.pausado ? 'pausado' : 'ativo';

  fantasmas.set(pid, g);
  desenharTabela();
  atualizarContadores();
}

function desenharFantasma(ctx, x, y, r, cor, dir, estado) {
  ctx.save();
  ctx.translate(x, y);
  if (estado === "pausado") ctx.globalAlpha = 0.6;
  ctx.fillStyle = cor;
  ctx.beginPath();
  ctx.arc(0, -r * 0.2, r, Math.PI, 0, false);
  ctx.lineTo(r, r * 0.7);
  const k = 5; const passo = (r * 2) / k;
  for(let i = 0; i < k; i++) ctx.arc(r - passo * (i + 0.5), r * 0.7, passo / 2, 0, Math.PI, true);
  ctx.closePath(); ctx.fill();
  const ex = Math.max(-1, Math.min(1, dir[0]));
  const ey = Math.max(-1, Math.min(1, dir[1]));
  const offX = ex * r * 0.20; const offY = ey * r * 0.20;
  function olho(cx, cy) {
    ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.ellipse(cx, cy, r * 0.28, r * 0.36, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#142a66"; ctx.beginPath(); ctx.arc(cx + offX, cy + offY, r * 0.15, 0, Math.PI * 2); ctx.fill();
  }
  olho(-r * 0.35, -r * 0.25); olho(r * 0.15, -r * 0.25);
  if (estado === "pausado") {
    ctx.fillStyle = "rgba(239, 68, 68, 0.8)"; ctx.beginPath(); ctx.arc(0, 0, r * 0.3, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "white"; ctx.font = "bold " + (r * 0.5) + "px Arial"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText("⏸", 0, 0);
  }
  ctx.restore();
}

function animar(){
  ctxA.clearRect(0, 0, cnvAtores.width, cnvAtores.height);
  ctxR.clearRect(0, 0, cnvRotulos.width, cnvRotulos.height);

  const t = performance.now();
  fantasmas.forEach((g, pid) => {
    const k = Math.max(0, Math.min(1, (t - g.t0) / (g.t1 - g.t0)));
    const x = g.x + (g.tx - g.x) * k;
    const y = g.y + (g.ty - g.y) * k;

    desenharFantasma(ctxA, x, y, tam * 0.45, g.cor, g.dir, g.estado);

    // Anel de progresso
    const frac = g.total ? (g.feito / g.total) : 0;
    ctxA.beginPath(); ctxA.arc(x, y, tam * 0.56, -Math.PI/2, -Math.PI/2 + frac * 2 * Math.PI); ctxA.lineWidth = 3; ctxA.strokeStyle = "#62ff99"; ctxA.stroke();

    // Etiqueta com nome e atividade
    const etiqueta = `${g.nome} • ${g.atividade || '-'}`;
    ctxR.font = "12px 'SF Mono', 'Monaco', monospace"; // GARANTE medida consistente
    const w = Math.max(140, ctxR.measureText(etiqueta).width + 12);
    const h = 16;
    ctxR.fillStyle = "rgba(15, 23, 42, 0.85)"; ctxR.fillRect(x - w/2, y - (tam * 0.95) - h, w, h);
    ctxR.fillStyle = "#e2e8f0"; ctxR.textAlign = "center"; ctxR.textBaseline = "bottom"; ctxR.fillText(etiqueta, x, y - (tam * 0.95));
  });

  requestAnimationFrame(animar);
}

function desenharTabela() {
  const tb = document.getElementById('tcorpo');
  if (fantasmas.size === 0) {
    tb.innerHTML = `
      <tr>
        <td colspan="6" class="empty-state">
          <i class="fas fa-ghost"></i>
          <p>Nenhum processo ativo</p>
        </td>
      </tr>`;
    return;
  }
  tb.innerHTML = '';
  fantasmas.forEach((g, pid) => {
    const ll = Math.round((g.y - tam/2) / tam);
    const cc = Math.round((g.x - tam/2) / tam);
    let classePilula = 'pending';
    if (g.feito === g.total) classePilula = 'completed';
    else if (g.feito > 0) classePilula = 'partial';
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${pid}</td>
      <td>${g.nome}</td>
      <td>${ll},${cc}</td>
      <td><span class="pilula ${classePilula}">${g.feito}/${g.total}</span></td>
      <td>${g.atividade || '-'}</td>
      <td>
        <button class="botao small warning" data-acao="parar" data-pid="${pid}"><i class="fas fa-pause"></i></button>
        <button class="botao small success" data-acao="continuar" data-pid="${pid}"><i class="fas fa-play"></i></button>
        <button class="botao small danger" data-acao="matar" data-pid="${pid}"><i class="fas fa-skull"></i></button>
      </td>`;
    tb.appendChild(tr);
  });
}

function atualizarContadores() {
  totalProcessos = fantasmas.size;
  processosParados = Array.from(fantasmas.values()).filter(g => g.estado === "pausado").length;
  atualizarEstatisticas();
}

// ################ LOGS: agora suportando TEXTO e HTML formatado ################
const logs = document.getElementById('logs');
function addLog(content, asHTML=false) {
  const div = document.createElement('div');
  div.className = 'log-entry';
  if (asHTML) {
    div.innerHTML = content; // usamos apenas com HTML gerado localmente
  } else {
    div.textContent = content; // conteúdo vindo do servidor permanece texto
  }
  logs.prepend(div);
  while (logs.childElementCount > 200) logs.removeChild(logs.lastChild);
}

// Event listeners para os botões (corrigido: usa closest para capturar o <button> mesmo se clicar no <i>)
document.addEventListener('click', async (ev) => {
  const btn = ev.target.closest('button[data-acao]');
  if (!btn) return;
  const pid = btn.getAttribute('data-pid');
  const acao = btn.getAttribute('data-acao');

  if (acao === 'parar' && fantasmas.has(parseInt(pid))) {
    fantasmas.get(parseInt(pid)).estado = "pausado";
  } else if (acao === 'continuar' && fantasmas.has(parseInt(pid))) {
    fantasmas.get(parseInt(pid)).estado = "ativo";
  }
  desenharTabela();
  atualizarContadores();

  try { await fetch(`/api/${acao}?pid=${pid}`, { method: 'POST' }); } catch (e) { console.error(e); }
});

document.getElementById('criarBtn').addEventListener('click', async () => {
  const n = Math.max(1, Math.min(20, parseInt(document.getElementById('qtd').value || '1', 10)));
  await fetch(`/api/novo?quantidade=${n}`, { method: 'POST' });
});

document.getElementById('pararTodosBtn').addEventListener('click', async () => {
  fantasmas.forEach((g) => { g.estado = "pausado"; });
  desenharTabela(); atualizarContadores();
  await fetch('/api/pararTodos', { method: 'POST' });
});

document.getElementById('continuarTodosBtn').addEventListener('click', async () => {
  fantasmas.forEach((g) => { g.estado = "ativo"; });
  desenharTabela(); atualizarContadores();
  await fetch('/api/continuarTodos', { method: 'POST' });
});

document.getElementById('matarTodosBtn').addEventListener('click', async () => {
  await fetch('/api/matarTodos', { method: 'POST' });
});

document.getElementById('limparLogsBtn').addEventListener('click', () => { logs.innerHTML = ''; });

document.getElementById('refreshBtn').addEventListener('click', () => { location.reload(); });

document.getElementById('ajudaBtn').addEventListener('click', () => {
  alert("Labirinto dos Processos\n\nCada fantasma representa um processo que precisa completar 3 tarefas (nos pontos C) antes de sair pelo S. O G é um gargalo controlado por semáforo onde apenas um processo pode passar por vez.\n\nUse os botões para controlar os processos individualmente ou em massa.");
});

// Inicialização do EventSource
const es = new EventSource('/eventos');
es.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.tipo === 'ola') {
    linhas = m.linhas; colunas = m.colunas; grade = m.labirinto.map(l => l.split(""));
    desenharLabirinto(); requestAnimationFrame(animar);
  }
  else if (m.tipo === 'instantaneo') {
    (m.dados || []).forEach(inserirOuAtualizar);
  }
  else if (m.tipo === 'agente') {
    inserirOuAtualizar(m.dados);
    if (m.dados.tipo === 'saida' || m.dados.tipo === 'fim') {
      processosFinalizados++; atualizarEstatisticas();
      setTimeout(() => { fantasmas.delete(m.dados.pid); desenharTabela(); }, 400);
    }
  }
  else if (m.tipo === 'log') {
    // Gera HTML formatado aqui (local, seguro) — não exibir tags cruas vindas do servidor
    const parts = m.linha.split(' | ');
    if (parts.length >= 3) {
      const time = parts[0];
      const processInfo = parts[1];
      const activity = parts.slice(2).join(' | ');
      const formattedLog = `
        <span class="log-time">${time}</span>
        <span class="log-name">${processInfo}</span>
        <span class="log-activity">${activity}</span>`;
      addLog(formattedLog, true);
    } else {
      addLog(m.linha, false);
    }
  }
  else if (m.tipo === 'logs') {
    logs.innerHTML = '';
    (m.dados || []).slice().reverse().forEach(line => addLog(line, false));
  }
};
</script>
</body>
</html>
"""

@app.get("/")
def indice():
    """Entrega a página HTML/JS da interface em português."""
    return Response(INDEX_HTML, mimetype="text/html")

# ===================== Inicialização =====================

def principal():
    # Inicia a thread que bombeia eventos para os assinantes SSE
    threading.Thread(target=bomba_eventos, daemon=True).start()
    # Cria alguns agentes iniciais (nascem em células caminháveis, longe de S/C/G)
    gerar(4)
    print("UI disponível em: http://localhost:5000")
    # Em Windows, certifique-se de executar via 'python -m' ou diretamente este script
    app.run(host="0.0.0.0", port=5000, debug=False)

if __name__ == "__main__":
    # No Windows (spawn), garanta o guard para evitar recursão na criação de processos
    principal()
