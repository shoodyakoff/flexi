# Шрифты

Все шрифты в этой папке распространяются под открытыми лицензиями, разрешающими повторное распространение.
Значения по умолчанию в `config.yaml` ссылаются на **Onest** (основной) и **Bebas Neue Cyrillic**
(акцентный), поэтому пайплайны субтитров/заголовков рендерятся из коробки.

| семейство | файл | лицензия |
|---|---|---|
| Onest | `Onest-Variable.ttf` | SIL OFL 1.1 (`OFL.txt`) |
| Bebas Neue Cyrillic | `BebasNeue-Cyrillic.ttf` | SIL OFL 1.1 (`BebasNeue-Cyrillic-OFL.txt`) |
| Comfortaa | `Comfortaa-Variable.ttf` | SIL OFL 1.1 (`Comfortaa-OFL.txt`) |
| Jura | `Jura-Variable.ttf` | SIL OFL 1.1 (`Jura-OFL.txt`) |
| Poiret One | `PoiretOne-Regular.ttf` | SIL OFL 1.1 (`PoiretOne-OFL.txt`) |
| Ruslan Display | `RuslanDisplay-Regular.ttf` | SIL OFL 1.1 (`RuslanDisplay-OFL.txt`) |

## Нужен другой вид (например, Gilroy / Druk Wide)?

В исходном проекте использовались коммерческие шрифты **Gilroy** (основной) и **Druk Wide
Cyr** (акцентный). Их **нельзя распространять**, поэтому они намеренно не
включены. Если у вас есть лицензия, положите `.ttf` в эту папку и укажите
в `config.yaml` имя семейства:

```yaml
fonts:
  base_family: "Gilroy"
  accent_family: "Druk Wide Cyr"
```

Шрифты определяются по токену имени семейства среди файлов в этой папке, поэтому любой
`.ttf`/`.otf`, который вы добавите, сразу становится доступен по имени семейства.
