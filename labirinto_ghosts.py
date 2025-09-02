#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Labirinto dos Processos — Fantasmas SSE v3 (versão em Português)
- UI em tempo real via Server-Sent Events (SSE)
- Fantasmas (Pac-Man) com animação suave + anel de progresso + legenda de atividade
- Labirinto GERADO (DFS): conectado; saída é a célula mais distante do início; 2 pontos de controle e 1 gargalo obrigatórios
- Processos reais (multiprocessing): parar/continuar/encerrar (SIGSTOP/SIGCONT/SIGTERM)
- Sincronização: semáforo no 'G'

Observações sobre a tradução para PT-BR:
- Todos os nomes de variáveis, funções, chaves de mensagens, endpoints e textos de UI foram traduzidos.
- O marcador da "saída" no grid foi alterado de 'E' (Exit) para 'S' (Saída).
- A estrutura geral e o comportamento permanecem equivalentes à versão original.
"""

import os, signal, time, random, json, threading, queue
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

def agente(nome, pos_inicial, fila_saida: FilaMP, gargalo: Semaphore, passo_ms=170):
    """Processo que caminha no labirinto, executa tarefas nos 'C' e finaliza ao chegar na 'S'."""
    pid = os.getpid()
    random.seed(pid ^ int(time.time()))
    l, c = pos_inicial
    concluido, total = 0, 3
    rodando = True

    def ao_terminar(_sig, _frm):
        nonlocal rodando
        rodando = False

    signal.signal(signal.SIGTERM, ao_terminar)

    def emitir(tipo, atividade):
        """Enfileira um evento para a UI/SSE com o estado atual do agente."""
        fila_saida.put({
            "tipo": tipo,
            "pid": pid,
            "nome": nome,
            "posicao": [l, c],
            "feito": concluido,
            "total": total,
            "atividade": atividade
        }, block=False)

    tarefas = [
        ("primos 170k", lambda: tarefa_primos(170000)),
        ("fibonacci 39", lambda: tarefa_fibo(39)),
        ("io 1.1s", lambda: tarefa_io(1.1))
    ]

    emitir("nascimento", "iniciando")

    while rodando:
        alvos = set(PONTOS) if concluido < total else {SAIDA}
        passo = proximo_passo_bfs((l, c), alvos)
        if not passo:
            # Sem caminho encontrado no momento (deveria ser raro). Move aleatório válido.
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
            gargalo.acquire()
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
parados = set()                       # PIDs em SIGSTOP
buffer_logs = deque(maxlen=500)       # histórico de logs para a UI
fila_eventos = FilaMP(maxsize=10000)  # fila principal de eventos dos agentes
sem_gargalo = Semaphore(1)            # semáforo do gargalo 'G'

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

def gerar(qtd=3):
    """Cria 'qtd' agentes em células livres, evitando S/C/G na largada."""
    evitar = {SAIDA, GARGALO, *PONTOS}
    candidatos = [pos for pos in LIVRES if pos not in evitar]
    random.shuffle(candidatos)
    for i in range(qtd):
        pos = candidatos[i % len(candidatos)]
        nome = f"Fantasma-{int(time.time()) % 10000}-{i + 1}"
        p = Process(target=agente, args=(nome, pos, fila_eventos, sem_gargalo, 170), daemon=True)
        p.start()
        filhos[p.pid] = (p, nome)
        _logar(f"{time.strftime('%H:%M:%S')} | gerenciador :: criado {nome}:{p.pid} em {pos}")

def _enviar_sinal(pid, sinal_obj):
    try:
        os.kill(pid, sinal_obj)
        return True
    except ProcessLookupError:
        return False

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
    if pid in filhos and _enviar_sinal(pid, signal.SIGSTOP):
        parados.add(pid)
        _logar(f"{time.strftime('%H:%M:%S')} | gerenciador :: SIGSTOP -> {pid}")
    return ("", 204)

@app.post("/api/continuar")
def api_continuar():
    pid = int(request.args.get("pid", "0"))
    if pid in filhos and _enviar_sinal(pid, signal.SIGCONT):
        parados.discard(pid)
        _logar(f"{time.strftime('%H:%M:%S')} | gerenciador :: SIGCONT -> {pid}")
    return ("", 204)

@app.post("/api/matar")
def api_matar():
    pid = int(request.args.get("pid", "0"))
    if pid in filhos and _enviar_sinal(pid, signal.SIGTERM):
        parados.discard(pid)
        _logar(f"{time.strftime('%H:%M:%S')} | gerenciador :: SIGTERM -> {pid}")
    return ("", 204)

# ===================== UI (HTML/JS) =====================

INDEX_HTML = r"""<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Labirinto dos Processos — Fantasmas SSE v3</title>
<style>
:root{color-scheme:dark}
body{margin:0;background:#0b0e14;color:#eaf0f7;font-family:Inter,system-ui,Arial,sans-serif;display:grid;grid-template-columns: minmax(740px, 1fr) clamp(380px, 33vw, 540px);height:100vh}
#esquerda{padding:14px}
#direita{padding:14px;border-left:1px solid #1b2130;overflow:auto}
#quadro{position:relative;width:fit-content;border:1px solid #1b2130;background:#0a0d12}
canvas{image-rendering:pixelated;display:block}
h1{margin:0 0 10px 0;font-size:20px}
.botao{padding:8px 10px;border-radius:8px;background:#1a2130;border:1px solid #2a3347;color:#eaf0f7;cursor:pointer}
.botao:hover{background:#202a3e}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}
th,td{border-bottom:1px solid #1b2130;padding:6px 6px;text-align:left}
.pilula{padding:2px 8px;border-radius:999px;background:#1a2130}
#painel{display:grid;grid-template-columns:1fr 120px;gap:8px;margin-bottom:8px}
input[type=number]{padding:8px;background:#0c0f14;border:1px solid #1b2130;color:#eaf0f7;border-radius:8px}
#logs{font:12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:#0a0c10;border:1px solid #1b2130;border-radius:8px;padding:8px;height:240px;overflow:auto}
.legenda{margin-top:8px;color:#a8b2c2;font-size:13px}
.etiqueta{font-size:11px;padding:2px 6px;border:1px solid #2a3347;background:#141925;border-radius:6px;color:#cfd7e6}
</style>
</head>
<body>
<div id="esquerda">
  <h1>Labirinto dos Processos — <span class="etiqueta">Fantasmas SSE v3</span></h1>
  <div id="quadro">
    <canvas id="labirinto"></canvas>
    <canvas id="atores" style="position:absolute;left:0;top:0;"></canvas>
    <canvas id="rotulos" style="position:absolute;left:0;top:0;pointer-events:none;"></canvas>
  </div>
  <div class="legenda"># Parede • . Caminho • <b>C</b> Ponto de Controle • <b>G</b> Gargalo • <b>S</b> Saída</div>
</div>
<div id="direita">
  <div id="painel">
    <input id="qtd" type="number" min="1" value="3">
    <button class="botao" id="criarBtn">Criar Novos</button>
  </div>
  <table>
    <thead><tr><th>PID</th><th>Nome</th><th>Posição</th><th>Progresso</th><th>Atividade</th><th>Ações</th></tr></thead>
    <tbody id="tcorpo"></tbody>
  </table>
  <h3>Logs</h3>
  <div id="logs"></div>
</div>

<script>
let linhas=0, colunas=0, grade=[];
const tam=22; // tamanho do ladrilho (px)
const cnvLab=document.getElementById('labirinto');
const cnvAtores=document.getElementById('atores');
const cnvRotulos=document.getElementById('rotulos');
const ctxL=cnvLab.getContext('2d');
const ctxA=cnvAtores.getContext('2d');
const ctxR=cnvRotulos.getContext('2d');

function desenharLabirinto(){
  cnvLab.width=colunas*tam; cnvLab.height=linhas*tam;
  cnvAtores.width=cnvLab.width; cnvAtores.height=cnvLab.height;
  cnvRotulos.width=cnvLab.width; cnvRotulos.height=cnvLab.height;
  for(let l=0;l<linhas;l++){
    for(let c=0;c<colunas;c++){
      const ch=grade[l][c];
      let cor="#ffffff";
      if(ch=='#') cor="#0b0d12";
      else if(ch=='C') cor="#f6a032";
      else if(ch=='G') cor="#8e6eea";
      else if(ch=='S') cor="#44cc77";
      ctxL.fillStyle=cor;
      ctxL.fillRect(c*tam, l*tam, tam, tam);
      if(ch=='.'){ ctxL.strokeStyle="#222a3b"; ctxL.lineWidth=0.5; ctxL.strokeRect(c*tam, l*tam, tam, tam); }
    }
  }
}

function centro(pos){ return [pos[1]*tam + tam/2, pos[0]*tam + tam/2]; }
function corHSL(pid){ const h=Math.abs(pid)%360; return `hsl(${h} 75% 55%)`; }

const fantasmas=new Map(); // pid -> estado
const DUR=170; // ms por passo (deve casar com o servidor)

function inserirOuAtualizar(msg){
  const pid=msg.pid;
  const g = fantasmas.get(pid) || {x:0,y:0,tx:0,ty:0,t0:performance.now(),t1:performance.now(),dir:[1,0],nome:msg.nome||`Fantasma-${pid}`,cor:corHSL(pid),feito:0,total:3,atividade:""};
  if(msg.posicao){
    const [nx,ny]=centro(msg.posicao);
    const dx = nx - (g.tx ?? nx), dy = ny - (g.ty ?? ny);
    const len = Math.hypot(dx,dy) || 1;
    g.dir=[dx/len, dy/len];
    g.x = g.tx ?? nx; g.y = g.ty ?? ny;
    g.tx=nx; g.ty=ny; g.t0=performance.now(); g.t1=g.t0 + DUR;
  }
  if(typeof msg.nome==='string') g.nome=msg.nome;
  if(typeof msg.feito==='number') g.feito=msg.feito;
  if(typeof msg.total==='number') g.total=msg.total;
  if(typeof msg.atividade==='string') g.atividade=msg.atividade;
  fantasmas.set(pid,g);
  desenharTabela();
}

function desenharFantasma(ctx, x, y, r, cor, dir){
  ctx.save(); ctx.translate(x,y);
  ctx.fillStyle=cor;
  ctx.beginPath();
  ctx.arc(0, -r*0.2, r, Math.PI, 0, false);
  ctx.lineTo(r, r*0.7);
  const k=5, passo=(r*2)/k;
  for(let i=0;i<k;i++){ ctx.arc(r - passo*(i+0.5), r*0.7, passo/2, 0, Math.PI, true); }
  ctx.closePath(); ctx.fill();
  const ex=Math.max(-1,Math.min(1,dir[0])), ey=Math.max(-1,Math.min(1,dir[1]));
  const offX=ex*r*0.20, offY=ey*r*0.20;
  function olho(cx,cy){
    ctx.fillStyle="#fff"; ctx.beginPath(); ctx.ellipse(cx,cy, r*0.28, r*0.36, 0, 0, Math.PI*2); ctx.fill();
    ctx.fillStyle="#142a66"; ctx.beginPath(); ctx.arc(cx+offX, cy+offY, r*0.15, 0, Math.PI*2); ctx.fill();
  }
  olho(-r*0.35, -r*0.25); olho(r*0.15, -r*0.25);
  ctx.restore();
}

function animar(){
  ctxA.clearRect(0,0,cnvAtores.width,cnvAtores.height);
  ctxR.clearRect(0,0,cnvRotulos.width,cnvRotulos.height);
  const t=performance.now();
  fantasmas.forEach((g,pid)=>{
    const k=Math.max(0,Math.min(1,(t-g.t0)/(g.t1-g.t0)));
    const x=g.x+(g.tx-g.x)*k, y=g.y+(g.ty-g.y)*k;
    desenharFantasma(ctxA, x, y, tam*0.45, g.cor, g.dir);
    const frac = g.total? (g.feito/g.total): 0;
    ctxA.beginPath(); ctxA.arc(x,y, tam*0.56, -Math.PI/2, -Math.PI/2 + frac*2*Math.PI);
    ctxA.lineWidth=3; ctxA.strokeStyle="#62ff99"; ctxA.stroke();
    const etiqueta = `${g.nome}  •  ${g.atividade||'-'}`;
    const w = Math.max(140, ctxR.measureText(etiqueta).width + 12);
    const h = 16;
    ctxR.fillStyle="rgba(0,0,0,0.55)";
    ctxR.fillRect(x-w/2, y-(tam*0.95)-h, w, h);
    ctxR.fillStyle="#dfe6f0"; ctxR.font="12px ui-monospace,monospace";
    ctxR.textAlign="center"; ctxR.textBaseline="bottom"; ctxR.fillText(etiqueta, x, y-(tam*0.95));
  });
  requestAnimationFrame(animar);
}

function desenharTabela(){
  const tb=document.getElementById('tcorpo'); tb.innerHTML='';
  fantasmas.forEach((g,pid)=>{
    const ll=Math.round((g.y-tam/2)/tam), cc=Math.round((g.x-tam/2)/tam);
    const tr=document.createElement('tr');
    tr.innerHTML = `
      <td>${pid}</td><td>${g.nome}</td><td>${ll},${cc}</td>
      <td><span class="pilula">${g.feito}/${g.total}</span></td>
      <td>${g.atividade||'-'}</td>
      <td>
        <button class="botao" data-acao="parar" data-pid="${pid}">parar</button>
        <button class="botao" data-acao="continuar" data-pid="${pid}">continuar</button>
        <button class="botao" data-acao="matar" data-pid="${pid}">matar</button>
      </td>`;
    tb.appendChild(tr);
  });
}

document.addEventListener('click', async (ev)=>{
  const t=ev.target; if(!t.matches('button[data-acao]')) return;
  const pid=t.getAttribute('data-pid'); const acao=t.getAttribute('data-acao');
  await fetch(`/api/${acao}?pid=${pid}`,{method:'POST'});
});
document.getElementById('criarBtn').addEventListener('click', async ()=>{
  const n=Math.max(1, Math.min(20, parseInt(document.getElementById('qtd').value||'1',10)));
  await fetch(`/api/novo?quantidade=${n}`,{method:'POST'});
});

const es = new EventSource('/eventos');
const logs=document.getElementById('logs');
function addLog(linha){
  const seguro=linha.replace(/[&<>]/g,s=>({ "&":"&amp;","<":"&lt;",">":"&gt;" }[s]));
  const div=document.createElement('div'); div.innerHTML=seguro; logs.prepend(div);
  while(logs.childElementCount>300) logs.removeChild(logs.lastChild);
}
es.onmessage = (e)=>{
  const m = JSON.parse(e.data);
  if(m.tipo==='ola'){ linhas=m.linhas; colunas=m.colunas; grade=m.labirinto.map(l=>l.split("")); desenharLabirinto(); requestAnimationFrame(animar); }
  else if(m.tipo==='instantaneo'){ (m.dados||[]).forEach(inserirOuAtualizar); }
  else if(m.tipo==='agente'){
    inserirOuAtualizar(m.dados);
    if(m.dados.tipo==='saida' || m.dados.tipo==='fim'){
      setTimeout(()=>fantasmas.delete(m.dados.pid) && desenharTabela(), 400);
    }
  }
  else if(m.tipo==='log'){ addLog(m.linha); }
  else if(m.tipo==='logs'){ logs.innerHTML=''; (m.dados||[]).slice().reverse().forEach(addLog); }
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
    app.run(host="0.0.0.0", port=5000, debug=False)

if __name__ == "__main__":
    principal()
