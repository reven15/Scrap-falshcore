# Scrap Flashscore — Odds

Scraper em Python (Playwright) que percorre a página de uma liga no
Flashscore, identifica os jogos de um determinado dia e recolhe as odds
de todos os mercados disponíveis (1X2, over/under, handicap, etc.) para
cada jogo, gravando tudo num único JSON.

## Aviso importante

- Os Termos de Serviço do Flashscore **proíbem scraping automatizado**.
  Isto é fornecido para fins educativos/uso pessoal — a responsabilidade
  de verificar se o uso que lhe vais dar é permitido é tua.
- O Flashscore usa proteções anti-bot que podem bloquear ou limitar
  acessos repetidos a partir do mesmo IP. Usa o `--delay` para espaçar
  pedidos e não corras isto em loop apertado.
- **Isto não foi testado contra o site ao vivo.** O ambiente onde este
  código foi escrito tem a rede bloqueada para domínios gerais (só
  passam pypi/npm/APIs internas), por isso não consegui validar os
  seletores CSS contra a página real. A estrutura do scraper e a lógica
  estão corretas, mas é bem possível que precises de corrigir 1 ou 2
  seletores em `SELECTORS` (topo de `scraper.py`) na primeira corrida.
  Usa a flag `--debug` (ver abaixo) para depurar rapidamente.

## Instalação

```bash
pip install -r requirements.txt
playwright install chromium
```

## Uso

```bash
python3 scraper.py \
  --league-url "https://www.flashscore.pt/futebol/portugal/liga-portugal/" \
  --date 15.09.2026 \
  --output odds.json
```

Argumentos:

- `--league-url` (obrigatório): URL da página da liga no Flashscore.
- `--date`: data dos jogos a extrair, formato `DD.MM.YYYY`. Default: hoje.
- `--output`: ficheiro JSON de saída (default `odds.json`).
- `--delay`: segundos de espera entre pedidos (default `1.5`).
- `--debug`: grava o HTML renderizado de cada página em `debug_html/`,
  para veres exatamente o que o Playwright está a encontrar.
- `--markets`: lista de mercados a extrair, separados por vírgula (ex.
  `--markets "1x2,mais-menos"`). Vazio (default) = todos os mercados
  disponíveis para o jogo.

## API HTTP (para ligar a outro pipeline)

`api.py` expõe o mesmo scraper via HTTP, pensado para outro sistema
disparar pedidos e ir consultando o resultado — um scrape de uma liga
inteira pode demorar minutos (um browser por pedido, um mercado de cada
vez), por isso o pedido de scrape é assíncrono: aceita já e devolve um
`job_id`, e o resultado consulta-se depois.

Correr localmente (sem Docker):

```bash
pip install -r requirements.txt
playwright install chromium
uvicorn api:app --host 0.0.0.0 --port 8000
```

### `POST /scrape`

Corpo (JSON):

```json
{
  "league_url": "https://www.flashscore.pt/futebol/portugal/liga-portugal/",
  "date": "15.09.2026",
  "markets": ["1x2", "mais-menos"],
  "delay": 1.5
}
```

Todos os campos exceto `league_url` são opcionais (`date` default hoje,
`markets` omitido = todos, `delay` default `1.5`). Resposta (`202
Accepted`):

```json
{"job_id": "ce90b592...", "status_url": "/jobs/ce90b592..."}
```

### `GET /jobs/{job_id}`

```json
{
  "job_id": "ce90b592...",
  "status": "done",
  "created_at": "2026-09-15T10:00:00",
  "request": {...},
  "result": { ... mesmo formato JSON descrito abaixo ... },
  "error": null
}
```

`status` é `pending` → `running` → `done` ou `error`. Em `error`,
`error` tem a mensagem da falha e `result` fica `null`.

**Limitação a saber:** os jobs vivem em memória — se o container
reiniciar, perdem-se os jobs a decorrer/terminados. Para um scraper
pessoal isto costuma ser aceitável; se precisares que sobrevivam a
reinícios, é preciso adicionar um armazenamento externo (ficheiro,
Redis, etc.), o que não está feito.

### `GET /health`

Devolve `{"status": "ok"}` — usado pelo `HEALTHCHECK` do Dockerfile.

## Docker (correr no home server)

```bash
docker compose up -d --build
```

Isto constrói a imagem (Python + Chromium via
`playwright install --with-deps chromium`) e arranca a API na porta
`8000`, como serviço persistente (`restart: unless-stopped`). Testa com:

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/scrape \
  -H "Content-Type: application/json" \
  -d '{"league_url": "https://www.flashscore.pt/futebol/portugal/liga-portugal/", "markets": ["1x2"]}'
```

Notas:

- **Não consegui testar o `docker build` nem o `docker compose up`**
  neste ambiente — o daemon Docker aqui não arranca (falta de
  privilégios do sandbox, não é um problema do Dockerfile). Testei em
  compensação: o Dockerfile usa a forma oficialmente documentada de
  instalar o Playwright + Chromium (`playwright install --with-deps`),
  o `docker-compose.yml` valida com `docker compose config` sem erros,
  e a API (`api.py`) foi testada a correr fora de Docker com pedidos
  HTTP reais (`/health`, `/scrape`, `/jobs/{id}`) — só falhou a parte de
  rede para o flashscore.pt, que é a mesma limitação de rede descrita
  acima. Corre `docker compose up -d --build` na tua máquina e avisa-me
  se der algum erro de build.
- `mem_limit: 1g` e `shm_size: 1gb` no compose são um ponto de partida
  — o Chromium é pesado; ajusta consoante o hardware do teu home
  server e quantos scrapes correm em paralelo (`_executor` em `api.py`
  está limitado a 2 workers em simultâneo por omissão).
- A porta `8000` fica exposta no host; se o pipeline que vai chamar
  isto correr no mesmo docker network, considera não publicar a porta
  para fora e ligar os dois serviços pelo nome do serviço
  (`flashscore-odds:8000`) dentro do mesmo `docker-compose.yml`/rede.

## Se os seletores estiverem desatualizados

1. Corre com `--debug`.
2. Abre `debug_html/league_page.html` (ou `match_<id>_sem_odds.html`) e
   inspeciona a classe/atributo do elemento que já não está a ser
   apanhado.
3. Atualiza o dicionário `SELECTORS` no topo de `scraper.py` — está
   centralizado exatamente para isto ser uma alteração de uma linha.

## Formato de saída

```json
{
  "league_url": "...",
  "date": "15.09.2026",
  "scraped_at": "2026-09-15T10:00:00",
  "matches": [
    {
      "match_id": "abc12345",
      "home": "Sporting",
      "away": "Benfica",
      "time": "20:30",
      "match_url": "https://www.flashscore.pt/jogo/abc12345/#/resumo-de-jogo",
      "odds": {
        "1x2-tempo-completo/tempo-completo": [
          {"bookmaker": "Bet365", "values": ["1.90", "3.40", "4.20"]}
        ],
        "mais-menos/tempo-completo": [
          {"bookmaker": "Bet365", "values": ["1.85", "1.95"]}
        ]
      }
    }
  ]
}
```

A chave dentro de `odds` identifica o mercado (extraída do próprio link
da aba no site); `values` mantém a ordem das colunas tal como aparecem
na tabela do Flashscore para esse mercado.
