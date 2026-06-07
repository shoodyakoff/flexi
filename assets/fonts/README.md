# Fonts

All fonts bundled here are under open licenses that permit redistribution.
`config.yaml` defaults reference **Onest** (body) and **Bebas Neue Cyrillic**
(accent) so the subtitle/heading pipelines render out of the box.

| family | file | license |
|---|---|---|
| Onest | `Onest-Variable.ttf` | SIL OFL 1.1 (`OFL.txt`) |
| Bebas Neue Cyrillic | `BebasNeue-Cyrillic.ttf` | SIL OFL 1.1 (`BebasNeue-Cyrillic-OFL.txt`) |
| Comfortaa | `Comfortaa-Variable.ttf` | SIL OFL 1.1 (`Comfortaa-OFL.txt`) |
| Jura | `Jura-Variable.ttf` | SIL OFL 1.1 (`Jura-OFL.txt`) |
| Poiret One | `PoiretOne-Regular.ttf` | SIL OFL 1.1 (`PoiretOne-OFL.txt`) |
| Ruslan Display | `RuslanDisplay-Regular.ttf` | SIL OFL 1.1 (`RuslanDisplay-OFL.txt`) |

## Want a different look (e.g. Gilroy / Druk Wide)?

The original project used the commercial fonts **Gilroy** (body) and **Druk Wide
Cyr** (accent). Those are **not redistributable**, so they are intentionally not
included. If you own a license, drop the `.ttf` into this folder and point
`config.yaml` at the family name:

```yaml
fonts:
  base_family: "Gilroy"
  accent_family: "Druk Wide Cyr"
```

Fonts resolve by family-name token against the files in this folder, so any
`.ttf`/`.otf` you add becomes usable by its family name immediately.
