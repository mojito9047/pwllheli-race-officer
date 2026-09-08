# Start-hut hardware documentation

> **Vendor manuals are not in this repository.** The manufacturer datasheets and
> user manuals for the camera, router, weather station and Victron power kit are
> other companies' copyrighted documents, so they are kept in the club's private
> archive rather than republished here. What remains is the club's own work: the
> hut wiring diagram and the horn control box drawing. Everything below describes
> where to file material in that private archive.

Reference material for the physical equipment in the Pwllheli start hut — the
race-office PC and everything wired to it. Keep vendor manuals, datasheets,
wiring diagrams, part numbers, photos and setup notes here so the kit can be
maintained, repaired or reinstalled without hunting for paperwork.

Suggested things to file here (as you have them):

- **Horn / signalling** — relay or serial horn interface, wiring, part numbers.
- **Cameras** — start/finish and hut cameras (model, PTZ, RTSP URLs, mounting).
- **Weather station** — model, mounting, network/serial details.
- **Off-grid power (Victron)** — SmartShunt, Phoenix IP43 charger, SmartSolar
  MPPT, battery, VE.Direct-to-USB cabling and COM-port assignments.
- **PC & networking** — the race-office PC spec, 4G/router, Cloudflare Tunnel.
- **Audio / VHF** — mixer, isolation transformer, radio audio interface.

Feel free to add subfolders (e.g. `horn/`, `camera/`, `power/`, `network/`) as
the collection grows.

## Filed so far

**power/** — off-grid power system
- `Manual_BMV_and_SmartShunt-pdf-en.pdf` — Victron SmartShunt battery monitor
- `Smart_IP43_Charger_120-240V-pdf-en.pdf` — Victron Phoenix Smart IP43 charger
- `MPPT_solar_charger_manual-en.pdf` — Victron SmartSolar MPPT charge controller
- `Manual-Orion-Tr-isolated-DC-DC-converters-EN-FR-NL-ES-IT-DE.pdf` / `Datasheet-Orion-Tr-DC-DC-converters-isolated-100-250-400W-EN.pdf` — Victron Orion-Tr DC-DC converter
- `Battery - AMPS_Safety_file.pdf` — battery safety data
- `Solar Panels - GPID_1500120546_TECH_00.pdf` — solar panel technical sheet

**camera/**
- `UD39407B-D_Network-Camera_User-Manual_G5-Web5.0_20260701.pdf` — network camera user manual
- `Camera - DS-2CD3786G2T-IZSY-H_Datasheet_20241120.pdf` — camera datasheet

**weather/**
- `Weather Station - WS69Manual.pdf` — WS69 weather station
- `Weather Station Gateway - FG-GW3010CA-ECO1.pdf` — weather station gateway

**network/**
- `Router - Datasheet_RUTX11_1.43.pdf` — Teltonika RUTX11 4G router

**horn/** — horn and its 12 V control/signalling wiring
- `Horn - 21.442.443 12-24 Declaration of Material Certificate 3.1.pdf` — horn material certificate
- `Horn Control Box.pdf` — horn control box
- `PSU 12V 20A.pdf` — 12 V 20 A power supply
- `HSR-12VDC.pdf` — 12 VDC relay
- `MK-S Relays.pdf` — MK-S relays
- `Fuse Holders.pdf` — fuse holders

**wiring/**
- `Hut Wiring Diagram - V0.2.pdf` — hut wiring diagram (PDF export; editable Visio source kept in Dropbox)
- `Wiring-Diagram-5025_5030.pdf` — component wiring diagram

> Source of these files: the club Dropbox `Hut_Shared/Manuals` folder. Vendor
> manuals/datasheets are third-party copyright, kept here for the club's own
> maintenance use in this private repository.
