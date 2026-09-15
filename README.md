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
