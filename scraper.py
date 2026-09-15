#!/usr/bin/env python3
"""Scraper de odds do Flashscore para os jogos de um dia numa liga.

Uso:
    python3 scraper.py --league-url "https://www.flashscore.pt/futebol/portugal/liga-portugal/" \
        --output odds.json

O Flashscore renderiza tudo via JavaScript e muda a marcação HTML com
alguma frequência. Os seletores abaixo estão centralizados em SELECTORS
para serem fáceis de corrigir caso deixem de encontrar elementos. Usa
--debug para gravar o HTML renderizado de cada página visitada, o que
ajuda a perceber rapidamente o que mudou.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("flashscore")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Seletores CSS candidatos. Ajusta aqui se o Flashscore mudar a marcação.
SELECTORS = {
    "cookie_accept": "#onetrust-accept-btn-handler, button[id*='accept-btn'], .cookie-accept",
    "match_row": "div.event__match",
    "match_time": ".event__time",
    "match_home": ".event__participant--home, .event__homeParticipant",
    "match_away": ".event__participant--away, .event__awayParticipant",
    "section_header": ".event__round, .wclLeagueHeader, .event__header",
    "odds_market_menu": "a[href*='comparacao-de-odds'], a[href*='odds-comparison']",
    "odds_table_row": ".ui-table__row",
    "odds_bookmaker": "a.oddsCell__bookmakerPart, .oddsCell__logo, span.oddsCell__bookmakerPart",
    "odds_cell_value": ".oddsCell__odd, span.oddsCell__odd, .oddsCell__value",
}


@dataclass
class Match:
    match_id: str
    home: str
    away: str
    time: str
    match_url: str
    odds: dict = field(default_factory=dict)


def _launch_browser(p):
    """Lança o Chromium, tentando primeiro a instalação normal do Playwright
    e caindo para o binário pré-instalado deste ambiente (se existir)."""
    env_path = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH")
    if env_path:
        return p.chromium.launch(headless=True, executable_path=env_path)
    try:
        return p.chromium.launch(headless=True)
    except Exception:
        fallback = "/opt/pw-browsers/chromium"
        if os.path.exists(fallback):
            log.warning("A usar o Chromium pré-instalado em %s", fallback)
            return p.chromium.launch(headless=True, executable_path=fallback)
        raise


def _accept_cookies(page: Page) -> None:
    try:
        page.locator(SELECTORS["cookie_accept"]).first.click(timeout=3000)
        log.debug("Banner de cookies aceite.")
    except PlaywrightTimeoutError:
        pass


def _dump_debug_html(page: Page, debug_dir: str, name: str) -> None:
    if not debug_dir:
        return
    os.makedirs(debug_dir, exist_ok=True)
    path = os.path.join(debug_dir, f"{name}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page.content())
    log.info("HTML de debug gravado em %s", path)


def get_matches_for_date(page: Page, league_url: str, target_date: str, debug_dir: str = "") -> list[Match]:
    """Devolve os jogos da liga cuja data (texto "DD.MM." mostrado pelo
    Flashscore para jogos que não são hoje, ou "Hoje" quando aplicável)
    corresponde a target_date (formato "DD.MM.YYYY")."""
    log.info("A abrir página da liga: %s", league_url)
    page.goto(league_url, wait_until="domcontentloaded", timeout=45000)
    _accept_cookies(page)
    try:
        page.wait_for_selector(SELECTORS["match_row"], timeout=20000)
    except PlaywrightTimeoutError:
        _dump_debug_html(page, debug_dir, "league_page_sem_jogos")
        raise RuntimeError(
            "Não encontrei nenhum jogo na página da liga. O seletor "
            f"'{SELECTORS['match_row']}' pode estar desatualizado — corre "
            "com --debug para gravar o HTML e ajustar SELECTORS['match_row']."
        )
    page.wait_for_timeout(1500)
    _dump_debug_html(page, debug_dir, "league_page")

    target_day_month = dt.datetime.strptime(target_date, "%d.%m.%Y").strftime("%d.%m.")
    today_str = dt.date.today().strftime("%d.%m.%Y")
    is_today = target_date == today_str

    rows = page.locator(SELECTORS["match_row"])
    count = rows.count()
    log.info("Encontrei %d jogos na página da liga (todas as datas).", count)

    matches: list[Match] = []
    current_date_label = None

    all_blocks = page.locator(
        f"{SELECTORS['match_row']}, {SELECTORS['section_header']}"
    )
    for i in range(all_blocks.count()):
        el = all_blocks.nth(i)
        class_attr = el.get_attribute("class") or ""
        if "event__match" not in class_attr:
            text = (el.inner_text() or "").strip()
            if text:
                current_date_label = text
            continue

        match_id_attr = el.get_attribute("id") or ""
        match_id = re.sub(r"^g_\d+_", "", match_id_attr) if match_id_attr else ""

        try:
            time_text = el.locator(SELECTORS["match_time"]).first.inner_text(timeout=2000).strip()
        except PlaywrightTimeoutError:
            time_text = ""

        # Quando o jogo é hoje, o Flashscore mostra só a hora (HH:MM);
        # quando é noutro dia, mostra "DD.MM. HH:MM".
        looks_like_today_only = bool(re.fullmatch(r"\d{2}:\d{2}", time_text))
        match_day_month = None
        m = re.match(r"(\d{2}\.\d{2}\.)", time_text)
        if m:
            match_day_month = m.group(1)

        belongs_to_target = False
        if match_day_month:
            belongs_to_target = match_day_month == target_day_month
        elif looks_like_today_only:
            belongs_to_target = is_today
        elif current_date_label:
            belongs_to_target = target_day_month in current_date_label or (
                is_today and "hoje" in current_date_label.lower()
            )

        if not belongs_to_target:
            continue

        try:
            home = el.locator(SELECTORS["match_home"]).first.inner_text(timeout=2000).strip()
        except PlaywrightTimeoutError:
            home = ""
        try:
            away = el.locator(SELECTORS["match_away"]).first.inner_text(timeout=2000).strip()
        except PlaywrightTimeoutError:
            away = ""

        if not match_id or not home or not away:
            log.warning("Jogo com dados incompletos ignorado (id=%s, home=%r, away=%r)", match_id, home, away)
            continue

        match_url = f"https://www.flashscore.pt/jogo/{match_id}/#/resumo-de-jogo"
        matches.append(Match(match_id=match_id, home=home, away=away, time=time_text, match_url=match_url))

    log.info("%d jogos correspondem à data %s.", len(matches), target_date)
    return matches


def get_odds_markets(page: Page, match_id: str, delay: float, debug_dir: str = "") -> dict[str, list[dict]]:
    """Abre a página de comparação de odds de um jogo e recolhe todos os
    mercados disponíveis no menu de abas."""
    odds_url = f"https://www.flashscore.pt/jogo/{match_id}/#/comparacao-de-odds/1x2-tempo-completo"
    log.info("  -> a abrir odds: %s", odds_url)
    page.goto(odds_url, wait_until="domcontentloaded", timeout=45000)
    _accept_cookies(page)

    try:
        page.wait_for_selector(SELECTORS["odds_market_menu"], timeout=15000)
    except PlaywrightTimeoutError:
        _dump_debug_html(page, debug_dir, f"match_{match_id}_sem_odds")
        log.warning("  Sem menu de mercados de odds para o jogo %s (pode não ter odds disponíveis).", match_id)
        return {}

    market_links = page.locator(SELECTORS["odds_market_menu"])
    markets: dict[str, tuple[str, str]] = {}
    for i in range(market_links.count()):
        link = market_links.nth(i)
        href = link.get_attribute("href") or ""
        m = re.search(r"comparacao-de-odds/([^/]+)/([^/?#]+)", href) or re.search(
            r"odds-comparison/([^/]+)/([^/?#]+)", href
        )
        if not m:
            continue
        market_key = f"{m.group(1)}/{m.group(2)}"
        markets[market_key] = (m.group(1), m.group(2))

    if not markets:
        markets["1x2/tempo-completo"] = ("1x2-tempo-completo", "tempo-completo")

    results: dict[str, list[dict]] = {}
    for market_key, (market, period) in markets.items():
        market_url = f"https://www.flashscore.pt/jogo/{match_id}/#/comparacao-de-odds/{market}/{period}"
        try:
            page.goto(market_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector(SELECTORS["odds_table_row"], timeout=8000)
        except PlaywrightTimeoutError:
            log.debug("  Sem tabela para o mercado %s no jogo %s", market_key, match_id)
            continue

        rows = page.locator(SELECTORS["odds_table_row"])
        market_odds = []
        for r in range(rows.count()):
            row = rows.nth(r)
            try:
                bookmaker = row.locator(SELECTORS["odds_bookmaker"]).first.inner_text(timeout=1000).strip()
            except PlaywrightTimeoutError:
                continue
            values_locator = row.locator(SELECTORS["odds_cell_value"])
            values = [values_locator.nth(v).inner_text().strip() for v in range(values_locator.count())]
            if bookmaker and values:
                market_odds.append({"bookmaker": bookmaker, "values": values})

        if market_odds:
            results[market_key] = market_odds
        time.sleep(delay)

    return results


def scrape_league_odds(
    league_url: str,
    target_date: str,
    delay: float = 1.5,
    debug_dir: str = "",
) -> dict:
    with sync_playwright() as p:
        browser = _launch_browser(p)
        context = browser.new_context(user_agent=USER_AGENT, locale="pt-PT")
        page = context.new_page()

        matches = get_matches_for_date(page, league_url, target_date, debug_dir)

        for idx, match in enumerate(matches, start=1):
            log.info("[%d/%d] %s vs %s (%s)", idx, len(matches), match.home, match.away, match.time)
            try:
                match.odds = get_odds_markets(page, match.match_id, delay, debug_dir)
            except Exception as exc:  # não abortar tudo por causa de um jogo
                log.error("  Falhou ao obter odds do jogo %s: %s", match.match_id, exc)
                match.odds = {}
            time.sleep(delay)

        browser.close()

    return {
        "league_url": league_url,
        "date": target_date,
        "scraped_at": dt.datetime.now().isoformat(timespec="seconds"),
        "matches": [
            {
                "match_id": m.match_id,
                "home": m.home,
                "away": m.away,
                "time": m.time,
                "match_url": m.match_url,
                "odds": m.odds,
            }
            for m in matches
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--league-url", required=True, help="URL da página da liga no Flashscore")
    parser.add_argument(
        "--date",
        default=dt.date.today().strftime("%d.%m.%Y"),
        help="Data dos jogos a extrair, formato DD.MM.YYYY (default: hoje)",
    )
    parser.add_argument("--output", default="odds.json", help="Ficheiro JSON de saída")
    parser.add_argument("--delay", type=float, default=1.5, help="Segundos de espera entre pedidos")
    parser.add_argument("--debug", action="store_true", help="Gravar HTML das páginas visitadas para debug")
    args = parser.parse_args()

    debug_dir = "debug_html" if args.debug else ""

    try:
        data = scrape_league_odds(args.league_url, args.date, args.delay, debug_dir)
    except RuntimeError as exc:
        log.error(str(exc))
        return 1

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    log.info("Guardado em %s (%d jogos)", args.output, len(data["matches"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
