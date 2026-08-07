"""Свойства решётки уникализации (`src/uniquify.py`).

Решётка раздаёт облик по номеру использования общего куска. Ломается она тихо:
файлы собираются, глазами всё в порядке, а гейт `qa_dedup.py` через час рендера
показывает пары за порогом. Эти тесты фиксируют свойства, которые уже были
куплены таким прогоном, чтобы следующая правка решётки не откатила их молча.

Числа порогов — из замеров, они записаны в `ДЕДУП_ИТОГИ_И_ДОРАБОТКИ.md` §2.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.uniquify import (  # noqa: E402
    BURNED_TEXT,
    DRIFT_PX,
    H,
    OVERSCALE,
    W,
    ZOOMS,
    look_for,
    overscale_for,
)

# Сколько раз за месяц используется один общий кусок: демо стоит через день,
# 16 раз из 31. Берём с запасом.
USES = 18

# 0.7° между двумя роликами хэш уже видит (0.72° мерялось в 72 бита из 256),
# 0.35° — почти нет (67% совпавших кадров).
TILT_OK = 0.7 - 1e-9


def _pairs():
    return [(i, j) for i in range(USES) for j in range(i + 1, USES)]


# ── чем разводятся ролики с общим демо ────────────────────────────────────────
# Историю стоит держать в голове, читая эти тесты. Сначала разводили кадр ролика
# (наклон + кроп + сдвиг рамки), и по хэшам это работало — но Стас забраковал
# глазами: на скринкасте срез 51–71 px со стороны обрезает края интерфейса и
# голову в углу, а наклон 0.65° заваливает горизонтальные линии на 12 px.
# Поэтому кадр ролика больше не трогается вообще (`DEFAULT` со всеми осями в
# нейтрали), а разводит ВСТАВКА демо: масштаб и положение вписанного в кадр
# демо. Ни один пиксель содержимого при этом не теряется.
# Замер пары с одним демо: вставка 0.94/−40 против 0.86/+60 — 0% совпавших
# кадров при медиане 82 бита (кроп с наклоном давал 2%/48, без геометрии 39%/34).

def test_кадр_ролика_не_трогается():
    """Решётка потоков должна быть в нейтрали — иначе вернётся срез кадра."""
    for i in range(USES):
        look = look_for(i)
        assert look.tilt == 0.0 and look.zoom == 1.0 and look.pan == (0, 0), (
            f"индекс {i}: кадр ролика снова трогают — {look.describe()}")
    assert overscale_for(look_for(0)) <= 1.005, "запас кадра больше 1 — значит режем"


def test_вставка_демо_не_повторяется():
    """Одно сочетание «масштаб + сдвиг» — не чаще раза на 9 использований.

    Демо повторяется 15–16 раз за месяц; девять сочетаний закрывают их так, что
    совпадение достаётся только паре индексов i и i+9.
    """
    from src.uniquify import inset_for

    seen: dict[tuple, list[int]] = {}
    for i in range(USES):
        seen.setdefault(inset_for(i), []).append(i)
    for combo, members in seen.items():
        gaps = [b - a for a, b in zip(members, members[1:])]
        assert all(g >= 9 for g in gaps), (
            f"вставка {combo} повторяется у индексов {members} — ближе 9 использований")


def test_вставка_ничего_не_срезает():
    """Любой масштаб вставки меньше единицы: демо целиком помещается в кадр."""
    from src.uniquify import INSET_SCALES

    assert max(INSET_SCALES) < 1.0, (
        f"масштаб {max(INSET_SCALES)} ≥ 1 — вставка начнёт вылезать за кадр")


def test_запас_кадра_постоянный():
    """Запас не должен зависеть от облика.

    Если считать его «сколько требует этот наклон и это окно», у широкого окна
    запас растёт вместе с окном и разница рамки между кропами схлопывается с 4%
    до 1.5% — то, что гейт поймал парой 16↔28.
    """
    values = {round(overscale_for(look_for(i)), 6) for i in range(USES)}
    assert len(values) == 1, f"запас кадра гуляет по обликам: {sorted(values)}"


def test_приближение_разнесено_по_кропам():
    """Реальное приближение (запас / кроп) — шаги не меньше 3%."""
    views = sorted(OVERSCALE / z for z in ZOOMS)
    steps = [b / a - 1 for a, b in zip(views, views[1:])]
    assert min(steps) >= 0.03, f"шаги приближения слишком мелкие: {steps}"


def test_рамка_повторяется_только_у_далёких_наклонов():
    """Одна рамка (кроп + сдвиг) может достаться двоим — но не с близким наклоном.

    Проверяется на ПАЛИТРЕ геометрии (`Grid()` со значениями по умолчанию), а не
    на решётке потоков: у потоков кадр сейчас не трогается вовсе. Свойство
    оставлено включённым, потому что палитрой пользуются другие материалы и к ней
    же вернутся, если геометрию когда-нибудь включат обратно — а куплено оно
    четырьмя прогонами, где замер гулял от 26% до 57% совпавших кадров.
    """
    from src.uniquify import Grid

    palette = Grid()
    frames: dict[tuple, list[int]] = {}
    for i in range(USES):
        look = look_for(i, palette)
        frames.setdefault((look.zoom, look.pan), []).append(i)
    for frame, members in frames.items():
        for a, i in enumerate(members):
            for j in members[a + 1:]:
                gap = abs(look_for(i, palette).tilt - look_for(j, palette).tilt)
                assert gap >= 0.9, (
                    f"индексы {i} и {j} делят рамку {frame}, а наклоны "
                    f"расходятся всего на {gap:.2f}°")


@pytest.mark.parametrize("index", range(USES))
def test_чёрные_углы_не_вылезают(index):
    """Окно со сдвигом и на любом краю дрейфа обязано лежать внутри кадра.

    Проверяются углы окна в системе координат повёрнутого кадра — центрального
    условия мало, потому что сдвиг и дрейф уводят окно из центра.
    """
    import math

    from src.uniquify import pan_room

    look = look_for(index)
    os_ = overscale_for(look)
    th = math.radians(abs(look.tilt))
    cw, ch = W * look.zoom, H * look.zoom
    room_x, room_y = pan_room(look)
    px, py = look.pan
    dx, dy = look.drift

    # Крайние положения центра окна: сдвиг ± половина хода дрейфа.
    for sx in (-1, 1):
        for sy in (-1, 1):
            ox = px * room_x + sx * dx * DRIFT_PX / 2
            oy = py * room_y + sy * dy * DRIFT_PX / 2
            half_w = (cw * math.cos(th) + ch * math.sin(th)) / 2
            half_h = (cw * math.sin(th) + ch * math.cos(th)) / 2
            reach_w = abs(ox * math.cos(th) + oy * math.sin(th))
            reach_h = abs(-ox * math.sin(th) + oy * math.cos(th))
            assert half_w + reach_w <= W * os_ / 2 + 1e-6, (
                f"индекс {index}: окно вылезает по ширине")
            assert half_h + reach_h <= H * os_ / 2 + 1e-6, (
                f"индекс {index}: окно вылезает по высоте")


# ── решётка для материала с чужими прожжёнными титрами ────────────────────────
# У «Весёлых» текст уже в исходнике и идёт почти в край: поля замерены в ~6.5%
# ширины, поэтому бюджет кропа здесь втрое меньше обычного. Свойства ниже тоже
# куплены прогоном — на блочной раздаче гейт дал 7 пар за порогом (худшая 50%
# совпавших кадров), потому что все августовские ролики одной семьи получили
# один и тот же кроп.

# Самая большая семья «Весёлых» — 11 роликов одного кадра.
FUN_USES = 12
TEXT_MARGIN = 0.065        # доля ширины до первой буквы самой длинной строки


def _fun_pairs():
    return [(i, j) for i in range(FUN_USES) for j in range(i + 1, FUN_USES)]


def test_мемы_кроп_не_съедает_текст():
    """Ни один облик не должен обрезать больше, чем поля текста."""
    for i in range(FUN_USES):
        look = look_for(i, BURNED_TEXT)
        crop = (1 - look.zoom / overscale_for(look, BURNED_TEXT)) / 2
        assert crop < TEXT_MARGIN, (
            f"индекс {i}: кроп {crop:.1%} со стороны при полях текста "
            f"{TEXT_MARGIN:.1%} — срежет буквы")


def test_мемы_близкие_наклоны_расходятся_кропом_или_темпом():
    for i, j in _fun_pairs():
        a, b = look_for(i, BURNED_TEXT), look_for(j, BURNED_TEXT)
        if abs(a.tilt - b.tilt) >= TILT_OK:
            continue
        assert a.zoom != b.zoom or a.tempo != b.tempo, (
            f"индексы {i} и {j}: наклоны {a.tilt} и {b.tilt} почти совпадают, "
            f"а кроп ({a.zoom}) и темп ({a.tempo}) одинаковые")


def test_мемы_запас_кадра_постоянный():
    values = {round(overscale_for(look_for(i, BURNED_TEXT), BURNED_TEXT), 6)
              for i in range(FUN_USES)}
    assert len(values) == 1, f"запас кадра гуляет по обликам: {sorted(values)}"


def test_мемы_три_оси_сразу_не_повторяются():
    """Наклон, кроп и темп втроём не должны совпасть ни у одной пары."""
    for i, j in _fun_pairs():
        a, b = look_for(i, BURNED_TEXT), look_for(j, BURNED_TEXT)
        assert (a.tilt, a.zoom, a.tempo) != (b.tilt, b.zoom, b.tempo), (
            f"индексы {i} и {j}: облик отличается только дрейфом")
