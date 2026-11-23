# Whirlpool-Regler

Eine Regelschicht für Home Assistant, die auf der [Gecko-Integration](https://github.com/LogixDE/ha-gecko-integration) aufsetzt. Der Whirlpool läuft dauerhaft im Watercare-Modus **Standard**. Den Eco-Betrieb macht der Regler selbst. Er ist der einzige, der den Gecko-Sollwert schreibt.

- **Drei Profile: Eco, Baden, Baden+.** Eco-Temperatur und Badetemperatur sind einzeln einstellbar. Der Regler-Thermostat schaltet zwischen den Profilen um und zeigt immer das echte Ziel des aktiven Profils, auch wenn der Gecko gerade einen abgesenkten oder gedeckelten Sollwert hat.
  - **Eco:** hält die Eco-Temperatur, nur Wärmepumpe.
  - **Baden:** heizt auf die Badetemperatur, nur Wärmepumpe.
  - **Baden+:** heizt auf die Badetemperatur, Heizstab erlaubt (schnell).
- **Hysterese gegen Takten der Wärmepumpe (im Eco-Profil).** Hat das Wasser die Eco-Temperatur erreicht, senkt der Regler den Gecko-Sollwert auf Ziel − Spanne − 0,5 K. Ist das Wasser um die Spanne abgekühlt, setzt er wieder das Ziel. Die Wärmepumpe heizt dann am Stück durch, statt alle paar Minuten kurz anzuspringen.
- **Heizstab sperren.** Im Modus Smart Heat schaltet der Gecko den Heizstab zu, wenn der Abstand zwischen Sollwert und Wasser groß ist. Außer in Baden+ und bei eingeschalteter PV-Freigabe ist der Heizstab gesperrt: Der Regler hält den Gecko-Sollwert bei Wasser + Abstand und zieht ihn mit dem Wasser nach oben, bis das Ziel erreicht ist.
- **Heizstab-Abwurf.** Der Gecko hält einen einmal gestarteten Heizstab auch bei kleinem Abstand an. Läuft er trotz Sperre länger als 60 s, setzt der Regler den Gecko-Sollwert kurz unter die Wassertemperatur, bis der Heizstab 30 s aus ist, und setzt danach wieder Wasser + Abstand.
- **Watercare-Modus festhalten.** Stellt jemand den Watercare-Modus in der App oder am Bedienteil um, schaltet der Regler nach einer Minute zurück auf Standard. In einem anderen Modus schreibt er nie.

Die Gecko-API bietet keinen direkten Zugriff auf den Heizstab oder den Wärmepumpenmodus. Die Sperre wirkt deshalb indirekt über den Sollwert und setzt **Smart Heat** (oder Smart Auto) am Gecko voraus.

## Entitäten

| Entität | Zweck |
|---|---|
| `climate.whirlpool_regler` | Profil (`eco` / `baden` / `baden_plus`) und dessen Zieltemperatur. *Aus* pausiert den Regler (Gecko regelt allein, der Regler schreibt nichts). |
| `number.whirlpool_regler_eco_temperatur` | Eco-Temperatur |
| `number.whirlpool_regler_badetemperatur` | Badetemperatur |
| `switch.whirlpool_regler_heizstab_freigabe_pv` | Gibt den Heizstab in jedem Profil frei. Gedacht für eine PV-Automation, die ihn ein- und ausschaltet, solange die Energiebilanz positiv ist. Wird nicht über Neustarts gespeichert. |
| `number.whirlpool_regler_hysterese_spanne` | Spanne der Hysterese, 0,5–2 K |
| `number.whirlpool_regler_abstand_bei_gesperrtem_heizstab` | Abstand Wasser → Sollwert bei gesperrtem Heizstab, 0,5–2 K |
| `switch.whirlpool_regler_hysterese` | Hysterese ein/aus |
| `sensor.whirlpool_regler_regelphase` | `wartet`, `pausiert`, `falscher_modus`, `heizen`, `heizen_ohne_heizstab`, `ziel_erreicht`, `abgesenkt`, `heizstab_abwurf` |
| `binary_sensor.whirlpool_regler_heizstab_aktiv` | Läuft der Heizstab gerade (aus `heat_source` des Gecko-Thermostats) |

Den Watercare-Modus, in dem der Whirlpool läuft, stellst du in den Optionen der Integration ein (Standard: Standard).

## Was der Regler an Gecko-Eigenheiten abfängt

- **Echos:** Nach einem Schreiben meldet der Gecko kurz den alten Wert zurück. Abweichungen in den 90 s nach einem eigenen Schreiben werden ignoriert.
- **Mitternachts-Blip:** Der Sollwert springt um Mitternacht für 1–2 s auf einen anderen Wert. Der Regler schreibt erst, wenn der gemeldete Sollwert 10 s stabil ist, und übernimmt Änderungen von außen erst nach 60 s Stabilität.
- **Veraltete Werte nach einem Moduswechsel:** Wurde der Watercare-Modus umgestellt, meldet der Gecko einige Sekunden noch den Sollwert des alten Modus. Der Regler wartet, bis Modus und Sollwert sich beruhigt haben.
- **Änderungen von außen:** Wer den Sollwert am Bedienteil oder in der App ändert, setzt damit das neue Ziel des aktiven Profils.
- **Verlorene Schreibvorgänge:** Bleibt der Gecko beim alten Wert, schreibt der Regler bis zu dreimal erneut, statt den alten Wert als Ziel zu übernehmen.
- **Verbindungsabbrüche:** Solange Thermostat oder Watercare-Modus nicht verfügbar sind, schreibt der Regler nichts.

## Installation

1. HACS → Benutzerdefinierte Repositories → `https://github.com/LogixDE/ha-whirlpool-regler`, Typ *Integration*.
2. *Whirlpool-Regler* installieren und Home Assistant neu starten.
3. Einstellungen → Geräte & Dienste → Integration hinzufügen → *Whirlpool-Regler*. Gecko-Thermostat und Watercare-Select wählen, optional einen eigenen Wassertemperatursensor.
4. Eco-Temperatur und Badetemperatur einstellen.

**Wichtig:** Andere Automationen dürfen weder den Gecko-Sollwert noch den Watercare-Modus schreiben. Sie bedienen stattdessen den Regler: Profil, Eco-/Badetemperatur und PV-Freigabe des Heizstabs.

## Entwicklung

```bash
pip install -r requirements_test.txt
python -m pytest
```

Die Regellogik (`logic.py`) ist frei von Home-Assistant-Importen und wird szenarienweise getestet; `test_integration.py` prüft die Einbindung gegen ein echtes Home Assistant.
