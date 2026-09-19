#!/usr/bin/env python3
"""One-off Playwright probe against /contentrewards/<exp>/app/.

Goal: open the first campaign card, click into its modal, and extract:
  - title / description
  - CPM per platform (TikTok, IG, YT Shorts, X, etc.)
  - Budget (total prize pool)
  - Reference Materials (links to Drive / Docs / Notion / YouTube / etc.)

Strategy:
  1. Goto the SPA URL, wait until at least one campaign anchor exists.
  2. Click the first campaign.
  3. Wait for the modal/dialog to open.
  4. Dump innerText + every anchor href in the modal so we can see structure.

Usage:
  source venv/bin/activate
  python scripts/whop_playwright_probe.py [--url URL] [--dump-html /tmp/probe.html]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path("/opt/clipping-system")
sys.path.insert(0, str(ROOT))

DEFAULT_URL = "https://whop.com/contentrewards/exp_B5C5S1vijHGVt9/app/"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--dump-html", default=None,
                    help="if set, write the modal HTML to this path for inspection")
    ap.add_argument("--timeout-ms", type=int, default=45000)
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

    summary: dict = {"url": args.url, "steps": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            ctx = browser.new_context(
                viewport={"width": 1400, "height": 900},
                user_agent=ua,
                locale="en-US",
            )
            page = ctx.new_page()

            page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            summary["steps"].append({"goto": "ok"})

            # Wait for at least one campaign anchor to appear (SPA renders).
            page.wait_for_selector('a[href*="/campaigns/"]', timeout=args.timeout_ms)
            # Give SPA another beat to lazy-load remaining cards.
            page.wait_for_timeout(2000)

            cards = page.evaluate(
                r"""
                () => {
                  const anchors = Array.from(document.querySelectorAll('a[href*="/campaigns/"]'));
                  const seen = new Set();
                  const out = [];
                  for (const a of anchors) {
                    if (seen.has(a.href)) continue;
                    seen.add(a.href);
                    out.push({
                      href: a.href,
                      text: (a.innerText || a.closest('[class]')?.innerText || '')
                              .replace(/\s+/g, ' ').trim().slice(0, 400),
                    });
                  }
                  return out;
                }
                """
            )
            summary["cards_on_listing"] = len(cards)
            summary["first_cards"] = cards[:5]
            summary["steps"].append({"list_cards": f"found {len(cards)}"})

            if not cards:
                print(json.dumps(summary, indent=2)[:2000])
                return 2

            # Click the first card. The anchor IS the card link.
            first_href = cards[0]["href"]
            summary["opening"] = first_href

            # Strategy: navigate directly to the campaign URL if it's a real
            # one; otherwise click and look for a modal/dialog.
            target_url = first_href
            page.goto(target_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            page.wait_for_timeout(2500)

            # Capture everything visible: body text + all anchors.
            modal_dump = page.evaluate(
                r"""
                () => {
                  // Try to find the campaign "modal" / detail panel.
                  const dialog = document.querySelector(
                    '[role="dialog"], [data-state="open"], .modal, [class*="Modal"], [class*="modal"]'
                  );
                  const root = dialog || document.body;
                  const anchors = Array.from(root.querySelectorAll('a[href]')).map(a => ({
                    text: (a.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 200),
                    href: a.href,
                  }));
                  return {
                    used_dialog: !!dialog,
                    body_text: (document.body.innerText || '').replace(/\s+/g, ' ').trim(),
                    root_text: (root.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 6000),
                    anchors,
                  };
                }
                """
            )

            summary["detail"] = {
                "url": page.url,
                "title": page.title(),
                "body_text_excerpt": (modal_dump.get("body_text") or "")[:4000],
                "root_text_excerpt": (modal_dump.get("root_text") or "")[:4000],
                "anchors_count": len(modal_dump.get("anchors") or []),
                "anchors": (modal_dump.get("anchors") or [])[:60],
            }
            summary["steps"].append({"detail_open": page.url})

            if args.dump_html:
                Path(args.dump_html).write_text(page.content(), errors="ignore")
                summary["steps"].append({"html_dump": args.dump_html})

        finally:
            browser.close()

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
