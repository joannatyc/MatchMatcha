#!/usr/bin/env python3
"""
丸久小山園抹茶補貨監測 → LINE 推播通知

環境變數：
  LINE_CHANNEL_ACCESS_TOKEN  LINE Messaging API 的 Channel access token（必填）
  LINE_USER_ID               要收通知的 LINE user ID（必填，U 開頭）
  WATCH_URLS                 要監測的網址，逗號分隔（預選：抹茶分類頁）
  WATCH_KEYWORDS             只通知名稱含這些字的商品，逗號分隔，不分大小寫（留空＝全部）
  STATE_FILE                 狀態檔路徑（預設 state.json）

用法：
  python monitor.py            檢查一次（GitHub Actions 用）
  python monitor.py --loop 300 每 300 秒檢查一次（在自己電腦上跑）
  python monitor.py --test     傳一則測試訊息到 LINE
  python monitor.py --debug    只印出抓到的商品與庫存狀態，不發通知
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

DEFAULT_URL = "https://www.marukyu-koyamaen.co.jp/english/shop/products/catalog/matcha"
TW = timezone(timedelta(hours=8))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
}
OUT_WORDS = ["out of stock", "sold out", "outofstock", "在庫切れ", "品切れ", "売り切れ", "完売", "入荷待ち"]
IN_WORDS = ["add to cart", "add to basket", "in stock", "instock", "カートに入れる", "購入する", "buy now"]
FAIL_ALERT_AFTER = 12  # 連續失敗幾次後傳一次 LINE 警告（5 分鐘一次 ≈ 1 小時）


def now_tw():
    return datetime.now(TW).strftime("%Y-%m-%d %H:%M")


# ---------- 抓取與解析 ----------

def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def classify(card):
    """回傳 'in' / 'out' / 'unknown'"""
    classes = " ".join(card.get("class", [])).lower()
    if "outofstock" in classes or "out-of-stock" in classes or "soldout" in classes:
        return "out"
    if "instock" in classes or "in-stock" in classes:
        return "in"
    text = card.get_text(" ", strip=True).lower()
    # 也看內部元素的 class（有些網站把狀態放在子元素）
    inner = " ".join(" ".join(e.get("class", [])) for e in card.find_all(True)).lower()
    blob = text + " " + inner
    if any(w in blob for w in OUT_WORDS):
        return "out"
    disabled_btn = card.select_one("button[disabled], .disabled, [aria-disabled='true']")
    if any(w in blob for w in IN_WORDS) and not disabled_btn:
        return "in"
    return "unknown"


def card_name(card, link):
    for sel in [".woocommerce-loop-product__title", ".product-title", ".product-name",
                ".name", ".title", "h2", "h3", "h4"]:
        el = card.select_one(sel)
        if el and el.get_text(strip=True):
            return el.get_text(" ", strip=True)
    img = card.find("img", alt=True)
    if img and img["alt"].strip():
        return img["alt"].strip()
    if link is not None and link.get_text(strip=True):
        return link.get_text(" ", strip=True)
    return "(未命名商品)"


def is_product_link(href):
    if not href:
        return False
    return "/shop/products/" in href and "/catalog/" not in href and "#" not in href.split("/")[-1]


def parse_page(html, page_url):
    """回傳 ({商品網址: {name, status}}, 下一頁網址或 None)"""
    soup = BeautifulSoup(html, "html.parser")
    items = {}

    cards = soup.select("li.product, div.product, article.product, .product-item, .item-product")
    if not cards:
        # 備案：從商品連結往上找卡片容器
        seen = set()
        for a in soup.find_all("a", href=True):
            if not is_product_link(a["href"]):
                continue
            card = a.find_parent(["li", "article"]) or a.find_parent("div")
            if card is not None and id(card) not in seen:
                seen.add(id(card))
                cards.append(card)

    for card in cards:
        link = None
        for a in card.find_all("a", href=True):
            if is_product_link(a["href"]):
                link = a
                break
        if link is None:
            continue
        url = urljoin(page_url, link["href"]).split("?")[0]
        status = classify(card)
        name = card_name(card, link)
        # 同一商品出現多次時，有貨優先
        if url not in items or status == "in":
            items[url] = {"name": name, "status": status}

    # 若這是單一商品頁（找不到商品卡片），把整頁當成一個商品
    if not items:
        title = soup.select_one("h1")
        body = soup.select_one("main") or soup.body or soup
        items[page_url] = {
            "name": title.get_text(" ", strip=True) if title else page_url,
            "status": classify(body),
        }

    nxt = soup.select_one("a.next, a[rel=next], .pagination a.next, link[rel=next]")
    next_url = urljoin(page_url, nxt["href"]) if nxt and nxt.get("href") else None
    return items, next_url


def parse_product(html):
    """解析單一商品頁。回傳 (status, {規格: True/False}, 判斷依據)"""
    soup = BeautifulSoup(html, "html.parser")

    # 1) WooCommerce 多規格商品：表單裡有每個規格的庫存 JSON
    form = soup.select_one("form.variations_form, form[data-product_variations]")
    if form and form.get("data-product_variations") not in (None, "", "false"):
        try:
            data = json.loads(form["data-product_variations"])
            variants = {}
            for v in data:
                label = " / ".join(str(x) for x in (v.get("attributes") or {}).values() if x) \
                        or str(v.get("variation_id", "?"))
                ok = bool(v.get("is_in_stock")) and v.get("is_purchasable", True) is not False
                variants[label] = variants.get(label, False) or ok
            if variants:
                return ("in" if any(variants.values()) else "out"), variants, "variations"
        except (ValueError, TypeError, AttributeError):
            pass

    # 2) 庫存文字標籤 <p class="stock in-stock / out-of-stock">
    labels = soup.select(".stock")
    cls = " ".join(" ".join(e.get("class", [])) for e in labels).lower()
    if "out-of-stock" in cls or "outofstock" in cls:
        if "in-stock" not in cls.replace("out-of-stock", ""):
            return "out", {}, "stock-label"
    if "in-stock" in cls.replace("out-of-stock", ""):
        return "in", {}, "stock-label"

    # 3) 商品主區塊的 class（instock / outofstock）
    main = soup.select_one("div[id^=product-], .product.type-product, main .product")
    if main is not None:
        c = " ".join(main.get("class", [])).lower()
        if "outofstock" in c:
            return "out", {}, "product-class"
        if "instock" in c:
            return "in", {}, "product-class"

    # 4) 加入購物車按鈕
    btn = soup.select_one("button.single_add_to_cart_button, button[name=add-to-cart], "
                          "input[name=add-to-cart], button[type=submit].add-to-cart")
    if btn is not None:
        disabled = btn.has_attr("disabled") or "disabled" in " ".join(btn.get("class", []))
        return ("out" if disabled else "in"), {}, "cart-button"

    # 5) 最後用文字判斷
    body = soup.select_one("main") or soup.body or soup
    return classify(body), {}, "text"


def scan(url, max_pages=10):
    items, page, n = {}, url, 0
    visited = set()
    while page and n < max_pages and page not in visited:
        visited.add(page)
        found, page = parse_page(fetch(page), page)
        items.update(found)
        n += 1
        if page:
            time.sleep(2)
    return items


# ---------- LINE ----------

def line_push(text):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    user = os.environ.get("LINE_USER_ID", "").strip()
    if not token or not user:
        print("!! 沒有設定 LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID，訊息只印在畫面上：\n" + text)
        return False
    chunks = [text[i:i + 4900] for i in range(0, len(text), 4900)][:5]
    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"to": user, "messages": [{"type": "text", "text": c} for c in chunks]},
        timeout=20,
    )
    if r.status_code != 200:
        print(f"!! LINE 推播失敗 {r.status_code}: {r.text}")
        return False
    return True


# ---------- 狀態 ----------

def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"items": {}, "fail_count": 0, "initialized": False}


def save_state(path, state):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


# ---------- 主流程 ----------

def check_once(debug=False):
    urls = [u.strip() for u in (os.environ.get("WATCH_URLS") or DEFAULT_URL).split(",") if u.strip()]
    keywords = [k.strip().lower() for k in os.environ.get("WATCH_KEYWORDS", "").split(",") if k.strip()]
    state_path = os.environ.get("STATE_FILE", "state.json")
    state = load_state(state_path)

    current = {}
    try:
        for u in urls:
            current.update(scan(u))
    except Exception as e:  # 網路錯誤、被擋等
        state["fail_count"] = state.get("fail_count", 0) + 1
        print(f"[{now_tw()}] 抓取失敗（連續 {state['fail_count']} 次）：{e}")
        if state["fail_count"] == FAIL_ALERT_AFTER and not debug:
            line_push(f"⚠️ 抹茶監測已連續 {FAIL_ALERT_AFTER} 次無法讀取網站，可能被擋或網站維護中。\n錯誤：{e}")
        if not debug:
            save_state(state_path, state)
        return

    if state.get("fail_count", 0) >= FAIL_ALERT_AFTER and not debug:
        line_push("✅ 抹茶監測已恢復，可以正常讀取網站了。")
    state["fail_count"] = 0

    if keywords:
        current = {u: v for u, v in current.items() if any(k in v["name"].lower() for k in keywords)}

    # 分類頁看不出庫存的商品，逐一打開商品頁檢查
    page_errors = 0
    for u, v in current.items():
        if v["status"] != "unknown" or u in urls:
            continue
        try:
            v["status"], v["variants"], v["source"] = parse_product(fetch(u))
        except Exception as e:
            page_errors += 1
            v["source"] = f"error: {e}"[:120]
        time.sleep(1)
    if page_errors:
        print(f"  有 {page_errors} 個商品頁讀取失敗，這次先略過")

    if debug:
        print(f"共找到 {len(current)} 項商品：")
        for u, v in sorted(current.items(), key=lambda x: x[1]["name"]):
            mark = {"in": "✅ 有貨", "out": "❌ 缺貨", "unknown": "❔ 不確定"}[v["status"]]
            print(f"  {mark}  {v['name']}  [{v.get('source', 'catalog')}]")
            for label, ok in (v.get("variants") or {}).items():
                print(f"          {'✅' if ok else '❌'} {label}")
            print(f"          {u}")
        return

    old = state.get("items", {})
    restocked = []
    for u, v in current.items():
        if v["status"] != "in":
            continue
        prev = old.get(u, {})
        prev_vars = prev.get("variants") or {}
        new_vars = [k for k, ok in (v.get("variants") or {}).items() if ok and not prev_vars.get(k)]
        if prev.get("status") != "in" or new_vars:
            restocked.append((u, v, new_vars))

    if not state.get("initialized"):
        in_stock = [v["name"] for v in current.values() if v["status"] == "in"]
        msg = f"🍵 抹茶補貨監測已啟動（{now_tw()}）\n監測 {len(current)} 項商品，目前有貨 {len(in_stock)} 項。"
        if in_stock:
            msg += "\n\n目前有貨：\n" + "\n".join(f"• {n}" for n in in_stock[:30])
        line_push(msg)
        state["initialized"] = True
    elif restocked:
        lines = [f"🍵 丸久小山園補貨了！（{now_tw()}）", ""]
        for u, v, new_vars in restocked:
            lines.append(f"• {v['name']}")
            if new_vars:
                lines.append("  規格：" + "、".join(new_vars))
            lines.append(f"  {u}")
        lines += ["", "快去搶 👉 " + urls[0]]
        line_push("\n".join(lines))

    print(f"[{now_tw()}] 檢查完成：{len(current)} 項，"
          f"有貨 {sum(v['status'] == 'in' for v in current.values())}，"
          f"不確定 {sum(v['status'] == 'unknown' for v in current.values())}，新補貨 {len(restocked)}")
    state["items"] = current
    save_state(state_path, state)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--loop", type=int, help="每 N 秒檢查一次")
    p.add_argument("--test", action="store_true", help="傳測試訊息")
    p.add_argument("--debug", action="store_true", help="只列出商品狀態")
    a = p.parse_args()

    if a.test:
        ok = line_push(f"🍵 測試成功！抹茶補貨通知會傳到這裡。（{now_tw()}）")
        sys.exit(0 if ok else 1)
    if a.loop:
        while True:
            check_once()
            time.sleep(max(60, a.loop))
    check_once(debug=a.debug)


if __name__ == "__main__":
    main()
