#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Labirinto dos Processos — Ghosts SSE v3 (estável)
- UI em tempo real via Server-Sent Events (SSE)
- Fantasmas (Pac-Man) com animação suave + anel de progresso + legenda de atividade
- Labirinto GERADO (DFS): conectado; saída é a célula mais distante do início; 2 checkpoints e 1 gargalo obrigatórios
- Processos reais (multiprocessing): stop/cont/kill (SIGSTOP/SIGCONT/SIGTERM)
- Sincronização: semáforo no 'G'
"""

import os, signal, time, random, json, threading, queue
from collections import deque
from multiprocessing import Process, Queue as MPQueue, Manager, Semaphore
from flask import Flask, Response, request

# ===================== Gerador de Labirinto (robusto) =====================
def _neighbors_2(r,c,rows,cols):
    for dr,dc in ((2,0),(-2,0),(0,2),(0,-2)):
        nr, nc = r+dr, c+dc
        if 1 <= nr < rows-1 and 1 <= nc < cols-1:
            yield nr,nc, dr//2, dc//2

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def generate_maze(rows=23, cols=43, checkpoints=2):
    """Gera labirinto perfeito e marca E/C/G no caminho principal com índices protegidos."""
    # garantir ímpares
    rows = rows if rows % 2 == 1 else rows+1
    cols = cols if cols % 2 == 1 else cols+1

    grid = [['#']*cols for _ in range(rows)]
    start = (1,1)
    grid[1][1] = '.'

    # DFS iterativo (backtracker)
    stack = [start]
    rng = random.Random(int(time.time()))
    while stack:
        r,c = stack[-1]
        choices = [(nr,nc,wr,wc) for (nr,nc,wr,wc) in _neighbors_2(r,c,rows,cols) if grid[nr][nc] == '#']
        if not choices:
            stack.pop(); continue
        nr,nc,wr,wc = rng.choice(choices)
        grid[r+wr][c+wc] = '.'
        grid[nr][nc] = '.'
        stack.append((nr,nc))

    # BFS a partir do início
    def bfs(src):
        from collections import deque as dq
        Q = dq([src]); dist={src:0}; prev={src:None}
        while Q:
            rr,cc = Q.popleft()
            for dr,dc in ((1,0),(-1,0),(0,1),(0,-1)):
                nr,nc = rr+dr, cc+dc
                if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc]=='.' and (nr,nc) not in dist:
                    dist[(nr,nc)] = dist[(rr,cc)] + 1
                    prev[(nr,nc)] = (rr,cc)
                    Q.append((nr,nc))
        return dist, prev

    dist, prev = bfs(start)
    # saída é a célula mais distante do início
    exit_cell = max(dist, key=dist.get)

    # caminho principal (start -> exit_cell)
    path = []
    cur = exit_cell
    while cur is not None:
        path.append(cur); cur = prev.get(cur)
    path.reverse()
    L = len(path)

    # segura contra casos patológicos (deveria ser grande, mas protegemos)
    if L < 3:
        # abre um pouco mais o labirinto e repete (fallback)
        return generate_maze(rows+2, cols+2, checkpoints)

    # coloca checkpoints em frações do caminho (clamp e sem colisão com extremos)
    checkpoints = max(1, checkpoints)
    Cs = []
    used = set()
    for k in range(1, checkpoints+1):
        idx = int(round(L * k/(checkpoints+1)))
        idx = clamp(idx, 1, L-2)
        # evita duplicados ajustando para o lado
        while idx in used and (1 <= idx <= L-2):
            idx = clamp(idx+1, 1, L-2)
            if idx in used:
                idx = clamp(idx-2, 1, L-2)
        used.add(idx)
        Cs.append(path[idx])

    # gargalo no meio do caminho, garantindo que não coincida com C nem extremos
    mid = clamp(L//2, 1, L-2)
    # se colidir, ajusta lateralmente
    offset = 0
    while path[mid] in Cs or mid in (0, L-1):
        offset += 1
        mid = clamp((L//2)+((-1)**offset)*offset, 1, L-2)
        if offset > L: break  # paranoia
    choke_cell = path[mid]

    # limpa marcas anteriores e grava C/G/E
    for r in range(rows):
        for c in range(cols):
            if grid[r][c] in ('C','E','G'):
                grid[r][c] = '.'
    for (rr,cc) in Cs: grid[rr][cc] = 'C'
    gr,gc = choke_cell; grid[gr][gc] = 'G'
    er,ec = exit_cell;  grid[er][ec] = 'E'

    # células caminháveis
    free = [(r,c) for r in range(rows) for c in range(cols) if grid[r][c] in ('.','C','G','E')]

    return grid, start, exit_cell, Cs, choke_cell, free

# --- gera um labirinto válido na inicialização
GRID, START, EXIT, CHECKS, CHOKE, FREE = generate_maze(rows=23, cols=43, checkpoints=2)
ROWS, COLS = len(GRID), len(GRID[0])

def cell(r,c): return GRID[r][c]
def walk(r,c): return cell(r,c) in ".CGE"
def inb(r,c): return 0 <= r < ROWS and 0 <= c < COLS
def neigh(r,c):
    for dr,dc in ((1,0),(-1,0),(0,1),(0,-1)):
        nr,nc = r+dr, c+dc
        if inb(nr,nc): yield nr,nc

# ===================== BFS próximo passo =====================
from collections import deque as dq
def bfs_next_step(src, targets:set):
    sr,sc = src
    if src in targets: return src
    Q = dq([src]); prev={src:None}; found=None
    while Q:
        r,c = Q.popleft()
        if (r,c) in targets: found = (r,c); break
        for nr,nc in neigh(r,c):
            if not walk(nr,nc): continue
            if (nr,nc) in prev: continue
            prev[(nr,nc)] = (r,c)
            Q.append((nr,nc))
    if not found: return None
    cur = found
    while prev[cur] and prev[cur] != src:
        cur = prev[cur]
    return cur

# ===================== Tarefas (CPU/IO) =====================
def task_primes(limit=170000):
    if limit<2: return 0
    sieve = bytearray(b"\x01")*(limit+1); sieve[0]=sieve[1]=0
    p=2
    while p*p<=limit:
        if sieve[p]:
            start=p*p; step=p
            sieve[start:limit+1:step]=b"\x00"*(((limit-start)//step)+1)
        p+=1
    return sum(sieve)

def task_fibo(n=39):
    a,b=0,1
    for _ in range(n): a,b=b,a+b
    return a

def task_io(sec=1.1):
    time.sleep(sec); return sec

# ===================== Agente (processo) =====================
def agent(name, start_pos, outQ:MPQueue, choke:Semaphore, step_ms=170):
    pid=os.getpid(); random.seed(pid ^ int(time.time()))
    r,c = start_pos
    done,total = 0,3
    running=True
    def on_term(sig,frm): 
        nonlocal running; running=False
    signal.signal(signal.SIGTERM, on_term)

    def emit(kind, activity):
        outQ.put({"kind":kind,"pid":pid,"name":name,"pos":[r,c],"done":done,"total":total,"activity":activity}, block=False)

    tasks=[("primes 170k",lambda:task_primes(170000)),
           ("fibonacci 39",lambda:task_fibo(39)),
           ("io 1.1s",lambda:task_io(1.1))]

    emit("spawn","starting")

    while running:
        targets = set(CHECKS) if done < total else {EXIT}
        step = bfs_next_step((r,c), targets)
        if not step:
            opts=[(nr,nc) for nr,nc in neigh(r,c) if walk(nr,nc)]
            if not opts: time.sleep(step_ms/1000); continue
            nr,nc=random.choice(opts)
        else:
            nr,nc = step

        if cell(nr,nc) == 'G':
            emit("state","waiting semaphore")
            choke.acquire()
            emit("state","entering choke")
            time.sleep(step_ms/1000)
            choke.release()
            emit("state","leaving choke")

        r,c = nr,nc
        emit("move","walking")

        if cell(r,c) == 'C' and done<total:
            tname,tfn = tasks[done]
            emit("state", f"task: {tname}")
            t0=time.time()
            try: _ = tfn()
            except Exception as ex:
                emit("state", f"task error: {ex}")
            dt=time.time()-t0
            done += 1
            emit("state", f"done ({tname}) in {dt:.2f}s")

        if (r,c)==EXIT and done>=total:
            emit("exit","finished"); break

        time.sleep(step_ms/1000)

    emit("end","terminated")

# ===================== Manager + SSE =====================
app = Flask(__name__)
manager = Manager()
shared   = manager.dict()      # pid -> estado
children = {}                  # pid -> (proc, name)
stopped  = set()               # PIDs em SIGSTOP
logbuf   = deque(maxlen=500)   # logs
eventQ   = MPQueue(maxsize=10000)
choke_sem = Semaphore(1)

# pub/sub simples para SSE
_subscribers=set()
_sub_lock=threading.Lock()

def _broadcast(obj:dict):
    data=json.dumps(obj, ensure_ascii=False)
    with _sub_lock:
        dead=[]
        for q in list(_subscribers):
            try: q.put_nowait(data)
            except queue.Full: dead.append(q)
        for q in dead:
            _subscribers.discard(q)

def _log(line:str):
    logbuf.appendleft(line)
    _broadcast({"kind":"log","line":line})

def pump_events():
    while True:
        ev = eventQ.get()
        pid = ev["pid"]
        shared[pid] = ev
        if ev["kind"] in ("spawn","state","exit","end"):
            _log(f"{time.strftime('%H:%M:%S')} | {ev['name']}:{pid} :: {ev['activity']} | pos={tuple(ev['pos'])} | {ev['done']}/{ev['total']}")
        _broadcast({"kind":"agent","data":ev})
        if ev["kind"] in ("exit","end"):
            proc = children.get(pid,(None,None))[0]
            if proc is not None: proc.join(timeout=0.1)
            children.pop(pid, None)
            stopped.discard(pid)

def spawn(n=3):
    # evite nascer em E/C/G
    avoid = {EXIT, CHOKE, *CHECKS}
    candidates = [pos for pos in FREE if pos not in avoid]
    random.shuffle(candidates)
    for i in range(n):
        start = candidates[i % len(candidates)]
        name  = f"Ghost-{int(time.time())%10000}-{i+1}"
        p = Process(target=agent, args=(name,start,eventQ,choke_sem,170), daemon=True)
        p.start()
        children[p.pid]=(p,name)
        _log(f"{time.strftime('%H:%M:%S')} | manager :: spawn {name}:{p.pid} at {start}")

def _sig(pid, sigobj):
    try: os.kill(pid, sigobj); return True
    except ProcessLookupError: return False

# ===================== Endpoints SSE/HTTP =====================
@app.get("/events")
def sse_events():
    q = queue.Queue(maxsize=2048)
    with _sub_lock:
        _subscribers.add(q)

    # estado inicial
    q.put_nowait(json.dumps({"kind":"hello","rows":ROWS,"cols":COLS,"maze":["".join(row) for row in GRID]}))
    q.put_nowait(json.dumps({"kind":"snapshot","data":list(shared.values())}))
    q.put_nowait(json.dumps({"kind":"logs","data":list(logbuf)}))

    def gen():
        try:
            while True:
                data = q.get()
                yield f"data: {data}\n\n"
        except GeneratorExit:
            with _sub_lock:
                _subscribers.discard(q)

    return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control":"no-cache", "X-Accel-Buffering":"no"})

@app.post("/api/new")
def api_new():
    count = max(1, min(20, int(request.args.get("count","1"))))
    spawn(count); return ("",204)

@app.post("/api/stop")
def api_stop():
    pid=int(request.args.get("pid","0"))
    if pid in children and _sig(pid, signal.SIGSTOP):
        stopped.add(pid); _log(f"{time.strftime('%H:%M:%S')} | manager :: SIGSTOP -> {pid}")
    return ("",204)

@app.post("/api/cont")
def api_cont():
    pid=int(request.args.get("pid","0"))
    if pid in children and _sig(pid, signal.SIGCONT):
        stopped.discard(pid); _log(f"{time.strftime('%H:%M:%S')} | manager :: SIGCONT -> {pid}")
    return ("",204)

@app.post("/api/kill")
def api_kill():
    pid=int(request.args.get("pid","0"))
    if pid in children and _sig(pid, signal.SIGTERM):
        stopped.discard(pid); _log(f"{time.strftime('%H:%M:%S')} | manager :: SIGTERM -> {pid}")
    return ("",204)

# ===================== UI (HTML/JS) =====================
INDEX = r"""<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Labirinto dos Processos — Ghosts SSE v3</title>
<style>
:root{color-scheme:dark}
body{margin:0;background:#0b0e14;color:#eaf0f7;font-family:Inter,system-ui,Arial,sans-serif;display:grid;grid-template-columns:minmax(860px,1fr) 380px;height:100vh}
#left{padding:14px}
#right{padding:14px;border-left:1px solid #1b2130;overflow:auto}
#wrap{position:relative;width:fit-content;border:1px solid #1b2130;background:#0a0d12}
canvas{image-rendering:pixelated;display:block}
h1{margin:0 0 10px 0;font-size:20px}
.btn{padding:8px 10px;border-radius:8px;background:#1a2130;border:1px solid #2a3347;color:#eaf0f7;cursor:pointer}
.btn:hover{background:#202a3e}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}
th,td{border-bottom:1px solid #1b2130;padding:6px 6px;text-align:left}
.pill{padding:2px 8px;border-radius:999px;background:#1a2130}
#panel{display:grid;grid-template-columns:1fr 120px;gap:8px;margin-bottom:8px}
input[type=number]{padding:8px;background:#0c0f14;border:1px solid #1b2130;color:#eaf0f7;border-radius:8px}
#logs{font:12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:#0a0c10;border:1px solid #1b2130;border-radius:8px;padding:8px;height:240px;overflow:auto}
.legend{margin-top:8px;color:#a8b2c2;font-size:13px}
.badge{font-size:11px;padding:2px 6px;border:1px solid #2a3347;background:#141925;border-radius:6px;color:#cfd7e6}
</style>
</head>
<body>
<div id="left">
  <h1>Labirinto dos Processos — <span class="badge">Ghosts SSE v3</span></h1>
  <div id="wrap">
    <canvas id="maze"></canvas>
    <canvas id="actors" style="position:absolute;left:0;top:0;"></canvas>
    <canvas id="labels" style="position:absolute;left:0;top:0;pointer-events:none;"></canvas>
  </div>
  <div class="legend"># Parede • . Caminho • <b>C</b> Checkpoint • <b>G</b> Gargalo • <b>E</b> Saída</div>
</div>
<div id="right">
  <div id="panel">
    <input id="qtd" type="number" min="1" value="3">
    <button class="btn" id="spawnBtn">Criar Novos</button>
  </div>
  <table>
    <thead><tr><th>PID</th><th>Nome</th><th>Pos</th><th>Progresso</th><th>Atividade</th><th>Ações</th></tr></thead>
    <tbody id="tbody"></tbody>
  </table>
  <h3>Logs</h3>
  <div id="logs"></div>
</div>

<script>
let rows=0, cols=0, grid=[];
const cell=22;
const maze=document.getElementById('maze');
const actors=document.getElementById('actors');
const labels=document.getElementById('labels');
const ctxM=maze.getContext('2d');
const ctxA=actors.getContext('2d');
const ctxL=labels.getContext('2d');

function drawMaze(){
  maze.width=cols*cell; maze.height=rows*cell;
  actors.width=maze.width; actors.height=maze.height;
  labels.width=maze.width; labels.height=maze.height;
  for(let r=0;r<rows;r++){
    for(let c2=0;c2<cols;c2++){
      const ch=grid[r][c2];
      let fill="#ffffff";
      if(ch=='#') fill="#0b0d12";
      else if(ch=='C') fill="#f6a032";
      else if(ch=='G') fill="#8e6eea";
      else if(ch=='E') fill="#44cc77";
      ctxM.fillStyle=fill;
      ctxM.fillRect(c2*cell, r*cell, cell, cell);
      if(ch=='.'){ ctxM.strokeStyle="#222a3b"; ctxM.lineWidth=0.5; ctxM.strokeRect(c2*cell, r*cell, cell, cell); }
    }
  }
}

function center(pos){ return [pos[1]*cell + cell/2, pos[0]*cell + cell/2]; }
function hsl(pid){ const h=Math.abs(pid)%360; return `hsl(${h} 75% 55%)`; }

const ghosts=new Map(); // pid -> state
const D=170; // ms por passo (igual ao servidor)

function upsert(msg){
  const pid=msg.pid;
  const g = ghosts.get(pid) || {x:0,y:0,tx:0,ty:0,t0:performance.now(),t1:performance.now(),dir:[1,0],name:msg.name||`Ghost-${pid}`,color:hsl(pid),done:0,total:3,activity:""};
  if(msg.pos){
    const [nx,ny]=center(msg.pos);
    const dx = nx - (g.tx ?? nx), dy = ny - (g.ty ?? ny);
    const len = Math.hypot(dx,dy) || 1;
    g.dir=[dx/len, dy/len];
    g.x = g.tx ?? nx; g.y = g.ty ?? ny;
    g.tx=nx; g.ty=ny; g.t0=performance.now(); g.t1=g.t0 + D;
  }
  if(typeof msg.name==='string') g.name=msg.name;
  if(typeof msg.done==='number') g.done=msg.done;
  if(typeof msg.total==='number') g.total=msg.total;
  if(typeof msg.activity==='string') g.activity=msg.activity;
  ghosts.set(pid,g);
  renderTable();
}

function drawGhost(ctx, x, y, r, color, dir){
  ctx.save(); ctx.translate(x,y);
  ctx.fillStyle=color;
  ctx.beginPath();
  ctx.arc(0, -r*0.2, r, Math.PI, 0, false);
  ctx.lineTo(r, r*0.7);
  const k=5, step=(r*2)/k;
  for(let i=0;i<k;i++){ ctx.arc(r - step*(i+0.5), r*0.7, step/2, 0, Math.PI, true); }
  ctx.closePath(); ctx.fill();
  const ex=Math.max(-1,Math.min(1,dir[0])), ey=Math.max(-1,Math.min(1,dir[1]));
  const offX=ex*r*0.20, offY=ey*r*0.20;
  function eye(cx,cy){
    ctx.fillStyle="#fff"; ctx.beginPath(); ctx.ellipse(cx,cy, r*0.28, r*0.36, 0, 0, Math.PI*2); ctx.fill();
    ctx.fillStyle="#142a66"; ctx.beginPath(); ctx.arc(cx+offX, cy+offY, r*0.15, 0, Math.PI*2); ctx.fill();
  }
  eye(-r*0.35, -r*0.25); eye(r*0.15, -r*0.25);
  ctx.restore();
}

function animate(){
  ctxA.clearRect(0,0,actors.width,actors.height);
  ctxL.clearRect(0,0,labels.width,labels.height);
  const t=performance.now();
  ghosts.forEach((g,pid)=>{
    const k=Math.max(0,Math.min(1,(t-g.t0)/(g.t1-g.t0)));
    const x=g.x+(g.tx-g.x)*k, y=g.y+(g.ty-g.y)*k;
    drawGhost(ctxA, x, y, cell*0.45, g.color, g.dir);
    const frac = g.total? (g.done/g.total): 0;
    ctxA.beginPath(); ctxA.arc(x,y, cell*0.56, -Math.PI/2, -Math.PI/2 + frac*2*Math.PI);
    ctxA.lineWidth=3; ctxA.strokeStyle="#62ff99"; ctxA.stroke();
    const label = `${g.name}  •  ${g.activity||'-'}`;
    const w = Math.max(140, ctxL.measureText(label).width + 12);
    const h = 16;
    ctxL.fillStyle="rgba(0,0,0,0.55)";
    ctxL.fillRect(x-w/2, y-(cell*0.95)-h, w, h);
    ctxL.fillStyle="#dfe6f0"; ctxL.font="12px ui-monospace,monospace";
    ctxL.textAlign="center"; ctxL.textBaseline="bottom"; ctxL.fillText(label, x, y-(cell*0.95));
  });
  requestAnimationFrame(animate);
}

function renderTable(){
  const tb=document.getElementById('tbody'); tb.innerHTML='';
  ghosts.forEach((g,pid)=>{
    const rr=Math.round((g.y-cell/2)/cell), cc=Math.round((g.x-cell/2)/cell);
    const tr=document.createElement('tr');
    tr.innerHTML = `
      <td>${pid}</td><td>${g.name}</td><td>${rr},${cc}</td>
      <td><span class="pill">${g.done}/${g.total}</span></td>
      <td>${g.activity||'-'}</td>
      <td>
        <button class="btn" data-act="stop" data-pid="${pid}">stop</button>
        <button class="btn" data-act="cont" data-pid="${pid}">cont</button>
        <button class="btn" data-act="kill" data-pid="${pid}">kill</button>
      </td>`;
    tb.appendChild(tr);
  });
}

document.addEventListener('click', async (ev)=>{
  const t=ev.target; if(!t.matches('button[data-act]')) return;
  const pid=t.getAttribute('data-pid'); const act=t.getAttribute('data-act');
  await fetch(`/api/${act}?pid=${pid}`,{method:'POST'});
});
document.getElementById('spawnBtn').addEventListener('click', async ()=>{
  const n=Math.max(1, Math.min(20, parseInt(document.getElementById('qtd').value||'1',10)));
  await fetch(`/api/new?count=${n}`,{method:'POST'});
});

const es = new EventSource('/events');
const logs=document.getElementById('logs');
function pushLog(line){
  const safe=line.replace(/[&<>]/g,s=>({ "&":"&amp;","<":"&lt;",">":"&gt;" }[s]));
  const div=document.createElement('div'); div.innerHTML=safe; logs.prepend(div);
  while(logs.childElementCount>300) logs.removeChild(logs.lastChild);
}
es.onmessage = (e)=>{
  const m = JSON.parse(e.data);
  if(m.kind==='hello'){ rows=m.rows; cols=m.cols; grid=m.maze.map(row=>row.split("")); drawMaze(); requestAnimationFrame(animate); }
  else if(m.kind==='snapshot'){ (m.data||[]).forEach(upsert); }
  else if(m.kind==='agent'){ upsert(m.data); if(m.data.kind==='exit'||m.data.kind==='end'){ setTimeout(()=>ghosts.delete(m.data.pid)&&renderTable(), 400); } }
  else if(m.kind==='log'){ pushLog(m.line); }
  else if(m.kind==='logs'){ logs.innerHTML=''; (m.data||[]).slice().reverse().forEach(pushLog); }
};
</script>
</body>
</html>
"""

@app.get("/")
def index():
    return Response(INDEX, mimetype="text/html")

def main():
    threading.Thread(target=pump_events, daemon=True).start()
    spawn(4)  # agentes iniciais (nascem em células caminháveis, longe de E/C/G)
    print("UI em: http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)

if __name__ == "__main__":
    main()