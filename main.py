import threading
import time

import flet as ft

import analyzer


def score_color(v):
    if v < 35:
        return ft.Colors.GREEN
    if v < 60:
        return ft.Colors.AMBER
    return ft.Colors.RED


def verdict_color(text):
    t = text.upper()
    if t.startswith("GIRME"):
        return ft.Colors.RED
    if t.startswith("BEKLE"):
        return ft.Colors.AMBER
    if "GIRIS ADAYI" in t:
        return ft.Colors.GREEN
    return ft.Colors.BLUE


def score_card(title, score, why, extra=""):
    lines = [ft.Text("• " + w, size=13) for w in (why or ["belirgin sinyal yok"])]
    head = ft.Row(
        [
            ft.Text(title + extra, weight=ft.FontWeight.BOLD, size=15),
            ft.Text(f"{score:.0f}/100", weight=ft.FontWeight.BOLD, color=score_color(score)),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )
    bar = ft.ProgressBar(value=min(1.0, score / 100), color=score_color(score))
    return ft.Card(content=ft.Container(padding=14, content=ft.Column([head, bar] + lines, spacing=8)))


def build_result(res):
    p = res["plan"]
    t = res["time"]
    ratio = t.get("vol_ratio")
    ratio_txt = "-" if ratio is None or ratio != ratio else f"{ratio:.2f}x"
    controls = []

    controls.append(
        ft.Card(
            content=ft.Container(
                padding=14,
                bgcolor=verdict_color(p["verdict"]),
                border_radius=10,
                content=ft.Column(
                    [
                        ft.Text("KARAR", size=12, color=ft.Colors.BLACK),
                        ft.Text(p["verdict"], size=17, weight=ft.FontWeight.BOLD, color=ft.Colors.BLACK),
                    ],
                    spacing=4,
                ),
            )
        )
    )

    controls.append(
        ft.Text(
            f"{res['symbol']}  |  fiyat {p['price']:.4f}  |  saat {res['local_now']}",
            size=13,
        )
    )

    controls.append(score_card("FOMO", res["fomo"], res["fomo_why"], f"  ({res['fomo_dir']})"))
    controls.append(score_card("Sahte hacim", res["fake"], res["fake_why"]))
    controls.append(score_card("Manipülasyon", res["manip"], res["manip_why"]))

    sup = ", ".join(f"{l:.4f}" for l, _ in p["supports"]) or "-"
    res_ = ", ".join(f"{l:.4f}" for l, _ in p["resistances"]) or "-"
    plan_lines = [
        ft.Text("Giriş planı", weight=ft.FontWeight.BOLD, size=15),
        ft.Text(f"Trend: {p['bias']}  |  ATR(1s): {p['atr']:.4f}", size=13),
        ft.Text(f"Giriş bölgesi: {p['zone'][0]:.4f} - {p['zone'][1]:.4f}", size=13),
        ft.Text(f"Stop: {p['stop']:.4f}", size=13),
        ft.Text(f"Hedef 1 / 2: {p['targets'][0]:.4f} / {p['targets'][1]:.4f}", size=13),
        ft.Text(f"R/R (hedef 1): {p['rr']:.1f}", size=13),
        ft.Text(f"15dk tetik: {'ONAYLI' if p['trigger'] else 'yok'}", size=13),
        ft.Text(f"Destek: {sup}", size=13),
        ft.Text(f"Direnç: {res_}", size=13),
    ]
    controls.append(ft.Card(content=ft.Container(padding=14, content=ft.Column(plan_lines, spacing=6))))

    nxt = "; ".join(f"{n} {tm} ({int(m)} dk)" for m, n, tm in t["next"])
    hot = ", ".join(f"{h} (%{r:.2f})" for h, r, _ in t["hot"])
    time_lines = [
        ft.Text("Kritik saatler", weight=ft.FontWeight.BOLD, size=15),
        ft.Text(f"Seans: {', '.join(t['sessions'])}  |  hacim/normal: {ratio_txt}", size=13),
        ft.Text(f"En oynak saatler: {hot}", size=13),
        ft.Text(f"Sıradaki: {nxt}", size=13),
    ]
    for w in t["warns"]:
        time_lines.append(ft.Text("! " + w, size=13, color=ft.Colors.AMBER))
    controls.append(ft.Card(content=ft.Container(padding=14, content=ft.Column(time_lines, spacing=6))))

    controls.append(
        ft.Text(
            "Yalnızca analizdir, yatırım tavsiyesi değildir. Skorlar olasılıksal sezgilerdir; "
            "kesin manipülasyon kanıtı değildir. Her zaman stop kullan.",
            size=11,
            italic=True,
        )
    )
    return controls


def main(page: ft.Page):
    page.title = "Kripto Analiz"
    page.theme_mode = ft.ThemeMode.DARK
    page.scroll = ft.ScrollMode.AUTO
    page.padding = 12

    symbol = ft.TextField(label="Sembol", value="BTCUSDT", expand=True)
    go = ft.ElevatedButton("Analiz et")
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
    status = ft.Text("", size=12)
    progress = ft.ProgressRing(visible=False, width=22, height=22)
    result = ft.Column(spacing=8)
    state = {"busy": False, "last": 0.0}

    def do_analysis(e=None):
        if state["busy"]:
            return
        state["busy"] = True
        go.disabled = True
        progress.visible = True
        status.value = "Veri çekiliyor..."
        page.update()
        try:
            res = analyzer.analyze(symbol.value, book=book.value, demo=demo.value)
            result.controls = build_result(res)
            status.value = f"Güncellendi: {res['local_now']}"
        except Exception as ex:
            status.value = f"Hata: {ex}"
        finally:
            state["busy"] = False
            state["last"] = time.time()
            go.disabled = False
            progress.visible = False
            page.update()

    go.on_click = do_analysis

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
        ft.Row([progress, status]),
        book,
        demo,
        auto,
        result,
    )


ft.app(target=main)
