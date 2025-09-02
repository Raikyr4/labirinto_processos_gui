# Labirinto dos Processos — Fantasmas SSE v3 (PT-BR)

Este projeto é uma simulação interativa em tempo real onde "fantasmas" (processos) percorrem um labirinto gerado aleatoriamente, executando tarefas de CPU/IO em pontos de controle, sincronizando a passagem em gargalos e finalizando ao alcançar a saída.  

A interface é exibida no navegador e atualizada via **Server-Sent Events (SSE)** em tempo real.

---

## ⚙️ Requisitos

- **Python 3.8+**
- **pip** (gerenciador de pacotes do Python)

---

## 📦 Instalação

Clone o repositório e entre na pasta do projeto:

```bash
git clone https://github.com/seuusuario/seurepositorio.git
cd seurepositorio
````

Crie e ative um ambiente virtual (opcional, mas recomendado):

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate
```

Instale as dependências:

```bash
pip install -r requirements.txt
```

Se você ainda não tiver um `requirements.txt`, crie-o com o seguinte conteúdo:

```txt
flask
```

---

## ▶️ Executando o projeto

No terminal, rode:

```bash
python labirinto.py
```

A saída mostrará algo como:

```
UI disponível em: http://localhost:5000
```

Abra o navegador e acesse:

👉 [http://localhost:5000](http://localhost:5000)

---

## 🕹️ Controles

Na interface web você poderá:

* Criar novos fantasmas (processos).
* Acompanhar a posição, progresso e atividade de cada fantasma.
* Parar, continuar ou encerrar processos via botões na tabela.
* Visualizar os logs em tempo real.

---

## 🗺️ Legenda do labirinto

* `#` → Parede
* `.` → Caminho
* `C` → Ponto de Controle (executa tarefa)
* `G` → Gargalo (controla acesso via semáforo)
* `S` → Saída

---

## 📖 Observações

* Cada fantasma é um processo real (`multiprocessing`) que pode ser pausado (`SIGSTOP`), continuado (`SIGCONT`) e finalizado (`SIGTERM`).
* O labirinto é gerado por DFS e sempre garante conectividade entre início e saída.
* A saída (`S`) é escolhida como a célula mais distante do ponto inicial (`(1,1)`).

---

