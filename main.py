import threading
import time

import flet as ft

import analyzer

STRETCH = ft.CrossAxisAlignment.STRETCH


def fmt(x):
    if x is None:
        return "-"
    x = float(x)
    if x >= 100:
        return f"{x:,.2f}"
    if x >= 1:
        return f"{x:.4f}"
    return f"{x:.6f}"


def fmt_qty(q):
    return f"{q:,.2f}" if q >= 1 else f"{q:.6f}"


def score_color(v):
    if v < 35:
        return ft.Colors.GREEN
    if v < 60:
        return ft.Colors.AMBER
    return ft.Colors.RED


def verdict_color(text):
    t = text.upper()
    if t.startswith("GIRME") or t.startswith("ONERILMEZ"):
        return ft.Colors.RED
    if t.startswith("BEKLE") or t.startswith("RISKLI"):
        return ft.Colors.AMBER
    if "GIRIS ADAYI" in t or t.startswith("UYGUN"):
        return ft.Colors.GREEN
    return ft.Colors.BLUE


def trend_color(t):
    return {"YUKARI": ft.Colors.GREEN, "ASAGI": ft.Colors.RED}.get(t, ft.Colors.GREY)


def card(controls, bgcolor=None):
    return ft.Card(
        content=ft.Container(
            padding=14,
            bgcolor=bgcolor,
            border_radius=10,
            content=ft.Column(controls, spacing=6, horizontal_alignment=STRETCH),
        )
    )


def title(text):
    return ft.Text(text, weight=ft.FontWeight.BOLD, size=15)


def line(text, color=None, size=13):
    return ft.Text(text, size=size, color=color)


def score_card(name, score, why, extra=""):
    head = ft.Row(
        [
            ft.Text(name + extra, weight=ft.FontWeight.BOLD, size=15),
            ft.Text(f"{score:.0f}/100", weight=ft.FontWeight.BOLD, color=score_color(score)),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )
    bar = ft.ProgressBar(value=min(1.0, score / 100), color=score_color(score))
    lines = [line("• " + w) for w in (why or ["belirgin sinyal yok"])]
    return card([head, bar] + lines)


def verdict_card(label, text):
    return card(
        [
            ft.Text(label, size=12, color=ft.Colors.BLACK),
            ft.Text(text, size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.BLACK),
        ],
        bgcolor=verdict_color(text),
    )


def build_result(res):
    p = res["plan"]
    t = res["time"]
    m = res["momentum"]
    tk = res.get("ticker")
    out = []

    out.append(verdict_card("KARAR - geri çekilme planı", p["verdict"]))
    if m.get("ok"):
        out.append(verdict_card("KARAR - momentum (kovalama) planı", m["verdict"]))

    head = f"{res['symbol']}  |  anlık {fmt(res['price'])}  |  veri {res.get('data_time', '-')}"
    out.append(line(head, size=13))
    if tk:
        out.append(line(f"24s değişim %{tk['chg']:+.2f}  |  24s hacim {tk['qvol'] / 1e6:,.2f}M USDT", size=12))

    if res.get("warnings"):
        out.append(card([title("Uyarılar")] + [line("! " + w, ft.Colors.AMBER) for w in res["warnings"]]))

    out.append(score_card("FOMO", res["fomo"], res["fomo_why"], f"  ({res['fomo_dir']})"))
    out.append(score_card("Sahte hacim", res["fake"], res["fake_why"]))
    out.append(score_card("Manipülasyon", res["manip"], res["manip_why"]))

    mtf = [title("Zaman dilimleri")]
    for r in res.get("mtf", []):
        mtf.append(
            ft.Row(
                [
                    ft.Text(r["tf"], size=13, width=50),
                    ft.Text(r["trend"], size=13, color=trend_color(r["trend"]), width=80),
                    ft.Text(f"RSI {r['rsi']:.0f}", size=13),
                ]
            )
        )
    out.append(card(mtf))

    pat = res.get("patterns", []) + res.get("divergence", [])
    out.append(card([title("Formasyon / uyumsuzluk (1s)")] + [line("• " + x) for x in (pat or ["belirgin formasyon yok"])]))

    sup = ", ".join(fmt(l) for l, _ in p["supports"]) or "-"
    rs = ", ".join(fmt(l) for l, _ in p["resistances"]) or "yok (fiyat zirvede)"
    out.append(
        card(
            [
                title("Geri çekilme planı"),
                line(f"Trend: {p['bias']}  |  ATR(1s): {fmt(p['atr'])}"),
                line(f"Giriş bölgesi: {fmt(p['zone'][0])} - {fmt(p['zone'][1])}"),
                line(f"Stop: {fmt(p['stop'])}"),
                line(f"Hedef 1 / 2: {fmt(p['targets'][0])} / {fmt(p['targets'][1])}"),
                line(f"R/R (hedef 1): {p['rr']:.1f}  (giriş bölgesinden hesaplı)"),
                line(f"15dk tetik: {'ONAYLI' if p['trigger'] else 'yok'}"),
                line(f"Destek: {sup}"),
                line(f"Direnç: {rs}"),
            ]
        )
    )

    if m.get("ok"):
        cap = "  (bakiye ile sınırlandı)" if m["capped"] else ""
        out.append(
            card(
                [
                    title("Momentum planı (anlık fiyattan)"),
                    line(f"Giriş: {fmt(m['entry'])}"),
                    line(f"Stop: {fmt(m['stop'])}  (fiyatın %{m['risk_pct_price']:.1f} altı)"),
                    line(f"Hedef 1 / 2: {fmt(m['t1'])} / {fmt(m['t2'])}"),
                    line(f"R/R: {m['rr1']:.1f} / {m['rr2']:.1f}"),
                    line(f"Pozisyon: {fmt_qty(m['qty'])} adet  ≈ {m['notional']:,.2f} USDT{cap}"),
                    line(f"Stop olursa kayıp: ≈ {m['risk_amt']:,.2f} USDT"),
                    line(m["trail"], size=12),
                ]
            )
        )
    else:
        out.append(card([title("Momentum planı"), line(m.get("note", "-"))]))

    ratio = t.get("vol_ratio")
    ratio_txt = "-" if ratio is None or ratio != ratio else f"{ratio:.2f}x"
    nxt = "; ".join(f"{n} {tm} ({int(mi)} dk)" for mi, n, tm in t["next"])
    hot = ", ".join(f"{h} (%{r:.2f})" for h, r, _ in t["hot"])
    tl = [
        title("Kritik saatler"),
        line(f"Seans: {', '.join(t['sessions'])}  |  hacim/normal: {ratio_txt}"),
        line(f"En oynak saatler: {hot}"),
        line(f"Sıradaki: {nxt}"),
    ]
    for w in t["warns"]:
        tl.append(line("! " + w, ft.Colors.AMBER))
    out.append(card(tl))

    out.append(
        ft.Text(
            "Yalnızca analizdir, yatırım tavsiyesi değildir. Skorlar olasılıksal sezgilerdir; "
            "kesin manipülasyon kanıtı değildir. Her zaman stop kullan.",
            size=11,
            italic=True,
        )
    )
    return out


def build_scan(rows):
    out = []
    for r in rows:
        if "error" in r:
            out.append(card([title(r["symbol"]), line("Hata: " + r["error"], ft.Colors.RED)]))
            continue
        out.append(
            card(
                [
                    ft.Row(
                        [title(r["symbol"]), line(fmt(r["price"]))],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    line(f"FOMO {r['fomo']:.0f}  |  Sahte {r['fake']:.0f}  |  Manip. {r['manip']:.0f}  |  {r['bias']}"),
                    line(r["verdict"], verdict_color(r["verdict"])),
                    line("Momentum: " + r["mom"], verdict_color(r["mom"]), size=12),
                ]
            )
        )
    return out


def main(page: ft.Page):
    page.title = "Kripto Analiz"
    page.theme_mode = ft.ThemeMode.DARK
    page.scroll = ft.ScrollMode.AUTO
    page.padding = ft.padding.only(left=12, right=12, top=40, bottom=16)

    def load(key, default):
        try:
            v = page.client_storage.get(key)
            return default if v is None else v
        except Exception:
            return default

    def save(key, value):
        try:
            page.client_storage.set(key, value)
        except Exception:
            pass

    symbol = ft.TextField(label="Sembol", value=load("symbol", "BTCUSDT"), expand=True)
    go = ft.ElevatedButton("Analiz et")
    balance = ft.TextField(
        label="Bakiye (USDT)", value=str(load("balance", "1000")), expand=True,
        keyboard_type=ft.KeyboardType.NUMBER,
    )
    risk = ft.TextField(
        label="Risk %", value=str(load("risk", "1")), expand=True,
        keyboard_type=ft.KeyboardType.NUMBER,
    )
    book = ft.Switch(label="Emir defteri kontrolü (+4 sn)", value=False)
    demo = ft.Switch(label="Demo veri", value=False)
    auto = ft.Dropdown(
        label="Otomatik yenile",
        value="0",
        options=[
            ft.dropdown.Option("0", "Kapalı"),
            ft.dropdown.Option("60", "1 dk"),
            ft.dropdown.Option("300", "5 dk"),
            ft.dropdown.Option("900", "15 dk"),
        ],
    )
    watch = ft.TextField(
        label="Tarama listesi (virgülle)", value=load("watch", "BTCUSDT,ETHUSDT,SOLUSDT"),
    )
    scan_btn = ft.ElevatedButton("Listeyi tara")
    tg_token = ft.TextField(label="Telegram bot token", value=load("tg_token", ""), password=True,
                            can_reveal_password=True)
    tg_chat = ft.TextField(label="Telegram chat id", value=load("tg_chat", ""))
    tg_on = ft.Switch(label="Karar değişince Telegram'a yaz", value=False)
    settings = ft.ExpansionTile(
        title=ft.Text("Bildirim ayarları"),
        controls=[tg_token, tg_chat, tg_on],
    )

    status = ft.Text("", size=12)
    progress = ft.ProgressRing(visible=False, width=22, height=22)
    result = ft.Column(spacing=8, horizontal_alignment=STRETCH)
    state = {"busy": False, "last": 0.0, "verdicts": {}}

    def num(tf, default):
        try:
            return float(str(tf.value).replace(",", "."))
        except Exception:
            return default

    def begin(msg):
        state["busy"] = True
        go.disabled = True
        scan_btn.disabled = True
        progress.visible = True
        status.value = msg
        page.update()

    def end():
        state["busy"] = False
        state["last"] = time.time()
        go.disabled = False
        scan_btn.disabled = False
        progress.visible = False
        page.update()

    def notify(res):
        if not (tg_on.value and tg_token.value and tg_chat.value):
            return
        sym = res["symbol"]
        v = res["plan"]["verdict"]
        prev = state["verdicts"].get(sym)
        state["verdicts"][sym] = v
        if prev is not None and prev != v:
            text = (
                f"{sym} {fmt(res['price'])}\n"
                f"{v}\nMomentum: {res['momentum'].get('verdict', '-')}\n"
                f"FOMO {res['fomo']:.0f} | Sahte {res['fake']:.0f} | Manip {res['manip']:.0f}"
            )
            try:
                analyzer.send_telegram(tg_token.value.strip(), tg_chat.value.strip(), text)
            except Exception as ex:
                status.value = f"Telegram hatası: {ex}"

    def do_analysis(e=None):
        if state["busy"]:
            return
        begin("Veri çekiliyor...")
        try:
            save("symbol", symbol.value)
            save("balance", balance.value)
            save("risk", risk.value)
            save("tg_token", tg_token.value)
            save("tg_chat", tg_chat.value)
            res = analyzer.analyze(
                symbol.value,
                book=book.value,
                demo=demo.value,
                balance=num(balance, 1000.0),
                risk_pct=num(risk, 1.0),
            )
            result.controls = build_result(res)
            status.value = f"Güncellendi: {res['local_now']}"
            notify(res)
        except Exception as ex:
            status.value = f"Hata: {ex}"
        finally:
            end()

    def do_scan(e=None):
        if state["busy"]:
            return
        begin("Liste taranıyor...")
        try:
            save("watch", watch.value)
            syms = [s for s in watch.value.replace(" ", "").split(",") if s]
            rows = analyzer.scan(
                syms, demo=demo.value, balance=num(balance, 1000.0), risk_pct=num(risk, 1.0)
            )
            result.controls = build_scan(rows)
            status.value = f"{len(rows)} coin tarandı"
        except Exception as ex:
            status.value = f"Hata: {ex}"
        finally:
            end()

    go.on_click = do_analysis
    scan_btn.on_click = do_scan

    def loop():
        while True:
            time.sleep(5)
            try:
                secs = int(auto.value or 0)
                if secs and not state["busy"] and time.time() - state["last"] >= secs:
                    do_analysis()
            except Exception:
                pass

    threading.Thread(target=loop, daemon=True).start()

    page.add(
        ft.Row([symbol, go]),
        ft.Row([balance, risk]),
        ft.Row([progress, status]),
        book,
        demo,
        auto,
        watch,
        scan_btn,
        settings,
        result,
    )


ft.app(target=main)
