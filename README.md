# Whirlpool-Regler

Eine Regelschicht für Home Assistant, die auf der [Gecko-Integration](https://github.com/LogixDE/ha-gecko-integration) aufsetzt. Der Regler ist der einzige Schreiber des Gecko-Sollwerts. Er stellt die Logik, die sonst über mehrere Automationen verteilt wäre, als Entitäten bereit:

- **Echte Zieltemperatur je Watercare-Modus.** Der Thermostat des Reglers zeigt immer das eigentliche Ziel, auch wenn der Gecko gerade einen abgesenkten oder gedeckelten Sollwert hat.
- **Hysterese gegen Takten der Wärmepumpe.** Hat das Wasser das Ziel erreicht, senkt der Regler den Gecko-Sollwert auf Ziel − Spanne − 0,5 K. Ist das Wasser um die Spanne abgekühlt, setzt er wieder das Ziel. Die Wärmepumpe heizt dann am Stück durch, statt alle paar Minuten kurz anzuspringen.
- **Heizstab sperren.** Im Modus Smart Heat schaltet der Gecko den Heizstab zu, wenn der Abstand zwischen Sollwert und Wasser groß ist. Ist der Heizstab gesperrt, hält der Regler den Gecko-Sollwert bei Wasser + Abstand und zieht ihn mit dem Wasser nach oben, bis das Ziel erreicht ist.

Läuft der Heizstab trotz Sperre weiter (der Gecko hält einen einmal gestarteten Heizstab auch bei kleinem Abstand an), wirft der Regler ihn nach 60 s ab: Er setzt den Gecko-Sollwert kurz unter die Wassertemperatur, bis der Heizstab 30 s aus ist, und setzt danach wieder Wasser + Abstand (Regelphase `heizstab_abwurf`).

Die Gecko-API bietet keinen direkten Zugriff auf den Heizstab oder den Wärmepumpenmodus. Die Sperre wirkt deshalb indirekt über den Sollwert und setzt **Smart Heat** (oder Smart Auto) am Gecko voraus.

## Entitäten

| Entität | Zweck |
|---|---|
| `climate.whirlpool_regler` | Zieltemperatur des aktuellen Watercare-Modus. Presets = Watercare-Modi. *Aus* pausiert den Regler (Gecko regelt allein, der Regler schreibt nichts). |
| `select.whirlpool_regler_heizstab` | `erlaubt` / `gesperrt` |
| `number.whirlpool_regler_hysterese_spanne` | Spanne der Hysterese, 0,5–2 K |
| `number.whirlpool_regler_abstand_bei_gesperrtem_heizstab` | Abstand Wasser → Sollwert bei gesperrtem Heizstab, 0,5–2 K |
| `switch.whirlpool_regler_hysterese` | Hysterese ein/aus |
| `sensor.whirlpool_regler_regelphase` | `wartet`, `pausiert`, `heizen`, `heizen_ohne_heizstab`, `ziel_erreicht`, `abgesenkt`, `heizstab_abwurf` |
| `binary_sensor.whirlpool_regler_heizstab_aktiv` | Läuft der Heizstab gerade (aus `heat_source` des Gecko-Thermostats) |

In welchen Watercare-Modi die Hysterese greift, stellst du in den Optionen der Integration ein (Standard: Away).

## Was der Regler an Gecko-Eigenheiten abfängt

- **Echos:** Nach einem Schreiben meldet der Gecko kurz den alten Wert zurück. Abweichungen in den 90 s nach einem eigenen Schreiben werden ignoriert.
- **Mitternachts-Blip:** Der Sollwert springt um Mitternacht für 1–2 s auf einen anderen Wert. Der Regler schreibt erst, wenn der gemeldete Sollwert 10 s stabil ist, und übernimmt Änderungen von außen erst nach 60 s Stabilität.
- **Sollwert je Modus:** Jeder Watercare-Modus hat am Gecko einen eigenen Sollwert. Ein Moduswechsel ist keine Änderung von außen. Direkt nach dem Wechsel meldet der Gecko einige Sekunden noch den Sollwert des alten Modus; der Regler übernimmt das Ziel eines neuen Modus deshalb erst, wenn Modus und Sollwert sich beruhigt haben. Der Regler schreibt erst, wenn der Modus 15 s stabil ist, damit nichts im falschen Modus landet.
- **Änderungen von außen:** Wer den Sollwert am Bedienteil oder in der App ändert, setzt damit das neue Ziel dieses Modus.
- **Verlorene Schreibvorgänge:** Bleibt der Gecko beim alten Wert, schreibt der Regler bis zu dreimal erneut, statt den alten Wert als Ziel zu übernehmen.
- **Verbindungsabbrüche:** Solange Thermostat oder Watercare-Modus nicht verfügbar sind, schreibt der Regler nichts.

## Installation

1. HACS → Benutzerdefinierte Repositories → `https://github.com/LogixDE/ha-whirlpool-regler`, Typ *Integration*.
2. *Whirlpool-Regler* installieren und Home Assistant neu starten.
3. Einstellungen → Geräte & Dienste → Integration hinzufügen → *Whirlpool-Regler*. Gecko-Thermostat und Watercare-Select wählen, optional einen eigenen Wassertemperatursensor.

Beim ersten Kontakt übernimmt der Regler den aktuellen Gecko-Sollwert jedes Modus als Ziel. Steht ein Modus gerade auf einem abgesenkten Wert, stell das Ziel danach einmal am Regler-Thermostat ein.

**Wichtig:** Andere Automationen dürfen den Gecko-Sollwert nicht mehr schreiben. Sie bedienen stattdessen den Regler-Thermostat, den Heizstab-Select oder den Watercare-Modus.

## Entwicklung

```bash
pip install -r requirements_test.txt
python -m pytest
```

Die Regellogik (`logic.py`) ist frei von Home-Assistant-Importen und wird szenarienweise getestet; `test_integration.py` prüft die Einbindung gegen ein echtes Home Assistant.
