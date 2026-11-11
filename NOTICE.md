# Notice

The **source code** in this repository is published under the MIT licence; see
[LICENSE](LICENSE). The rest of this file is about the things that licence does
not cover.

## Logos and other marks are not covered

The repository contains images that are not CapeNet's to license, and the MIT
licence above does not grant any right to use them:

| | |
|---|---|
| `static/img/pwllheli_sailing_club_logo*.png`, `static/img/psc_public_header.png`, `static/img/favicon.*`, `data/branding/club_logo.png` | Pwllheli Sailing Club's marks |
| `data/branding/sponsor_*.png` | the marks of the club's sponsors, used with the club's arrangement with them and not under any licence given here |

They are in the tree because the app ships with Pwllheli's branding as its
default. **If you run this software for another club, replace them.** Branding —
club name, short name, logo and public header — is configurable from
**Settings → Club name and branding**, so nothing needs editing in code to do
it; see [docs/CLUB_CUSTOMISATION.md](docs/CLUB_CUSTOMISATION.md).

Using someone else's logo can imply an endorsement they have not given. The
sponsor images especially are there for Pwllheli's own deployment and for no
other purpose.

## Club configurations

`club_configs/` holds marks, courses and start/finish definitions contributed
for particular clubs. They describe real water and real buoys at those clubs,
and they are published so the configuration format has worked examples. They are
not navigational data and must not be used as such.

## The name

"Pwllheli Race Officer" is the name of this application as the club runs it. The
MIT licence lets you take the code and do as you wish with it; it does not
oblige you to keep the name, and a substantially changed version is clearer to
everyone under a different one.

## Bundled third-party fonts

`static/fonts/` carries Archivo, Archivo Narrow and IBM Plex Mono, each under the
SIL Open Font Licence 1.1 rather than the MIT licence above; the licence
text travels with them in `static/fonts/OFL.txt`. They are bundled
rather than fetched from a font CDN because the start hut reaches the internet
through a tunnel, and race morning is the wrong time to discover a third-party
dependency.
