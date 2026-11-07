# Toshiba Link for Home Assistant

[![GitHub release](https://img.shields.io/github/v/release/anto294/homeassistant-toshiba-ac?style=plastic)](https://github.com/anto294/homeassistant-toshiba-ac/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/anto294/homeassistant-toshiba-ac/ci.yml?style=plastic)](https://github.com/anto294/homeassistant-toshiba-ac/actions)
![Downloads](https://img.shields.io/github/downloads/anto294/homeassistant-toshiba-ac/total?style=plastic)

This integration lets you monitor and control Toshiba air conditioners with a Wi-Fi module or adapter from Home Assistant: mode, temperature, fan, vanes, power presets, comfort functions, temperatures and energy use. It uses the same cloud service as the Toshiba app, and updates arrive as soon as the unit reports them.

> [!NOTE]
> Toshiba Link started as a fork of [h4de5/home-assistant-toshiba_ac](https://github.com/h4de5/home-assistant-toshiba_ac), and includes work from [vmvelev/home-assistant-toshiba_ac](https://github.com/vmvelev/home-assistant-toshiba_ac). It is a separate integration with its own `toshiba_ac_link` domain, so please report problems [here](https://github.com/anto294/homeassistant-toshiba-ac/issues), not to those projects. See [Differences from those projects](#differences-from-those-projects).

## Contents

- [What you can do with it](#what-you-can-do-with-it)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Entities](#entities)
- [How data is updated](#how-data-is-updated)
- [Actions](#actions)
- [Automation examples](#automation-examples)
- [Supported devices](#supported-devices)
- [Known limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)
- [Removal](#removal)
- [Translations](#translations)
- [Migrating from another Toshiba integration](#migrating-from-another-toshiba-integration)
- [Differences from those projects](#differences-from-those-projects)
- [Documentation for developers](#documentation-for-developers)
- [Quality scale](#quality-scale)
- [Credits and license](#credits-and-license)

## What you can do with it

- **Automate comfort:** heat the living room before you get home, or turn everything off when the last person leaves.
- **Protect an empty home:** turn on the 8 °C mode from an automation when you leave for the winter.
- **Keep nights quiet:** set the outdoor unit to silent mode at bedtime.
- **Follow energy use:** add each unit to the Energy dashboard, if the unit reports its consumption.

## Requirements

| Component | Minimum version |
|---|---|
| Home Assistant | 2026.11.0 |

You also need:

- A Toshiba unit that works with the Toshiba app, through a built-in Wi-Fi module or an adapter.
- The account you use in the app. Add new units in the app: this integration cannot do it.
- Internet access. The units only talk to the Toshiba cloud, so this integration cannot work on the local network alone.

## Installation

### Marketplace (recommended)

Since Home Assistant 2026.11, HACS is built into Home Assistant as the Marketplace. Toshiba Link is not listed in its catalog yet, so open it directly:

[![Open your Home Assistant instance and open a repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=anto294&repository=homeassistant-toshiba-ac&category=integration)

Select **Install**, confirm, and restart Home Assistant if a repair asks you to.

### Manual

Download `toshiba_ac_link.zip` from the [latest release](https://github.com/anto294/homeassistant-toshiba-ac/releases/latest), extract it into `config/custom_components/toshiba_ac_link/` and restart Home Assistant.

### Updating

The Marketplace offers a new version as an update under **Settings → Updates**. After you install it, restart Home Assistant. After a manual installation, replace the contents of the folder and restart.

## Configuration

Go to **Settings → Devices & services → Add integration** and search for **Toshiba Link**.

| Field | Description |
|---|---|
| Username | The account you use in the Toshiba app |
| Password | The password of that account |

Each account can be added once. There are no other options.

> [!IMPORTANT]
> Toshiba allows **one active connection per account**. If another Home Assistant, or another Toshiba integration, uses the same account, connections get refused, with an error that looks like a wrong password. See [Troubleshooting](#troubleshooting).

When the password stops working, Home Assistant asks for the new one under **Settings → Devices & services**.

## Entities

Each unit becomes a device. An entity only exists if the unit has the function.

| Entity | Type | Notes |
|---|---|---|
| *(device name)* | Climate | Mode, target temperature, fan, vertical swing, power preset. Horizontal swing on units with independent vanes |
| Indoor temperature | Sensor | |
| Outdoor temperature | Sensor | Unavailable while the outdoor unit reports nothing |
| Energy consumption | Sensor | In Wh since the start of the year. Works with the Energy dashboard |
| Self cleaning | Binary sensor | On while the unit is cleaning itself. The climate entity shows off during this time, as the app does |
| Outdoor unit silent mode | Select | Off, Silent 1, Silent 2 |
| Fireplace mode | Select | Off, Fireplace 1, Fireplace 2 |
| 8 °C mode | Switch | Frost protection, with a target of 5 to 13 °C, or to 16 °C on model 3 units |
| Air purifier | Switch | |
| ECO mode | Switch | |
| High power mode | Switch | |
| Wireless LED | Switch | Configuration entity, also works while the unit is off |

Outside the 8 °C mode, the target temperature goes from 17 to 30 °C. Except for the wireless LED, switches and selects are unavailable when they do not apply, such as while the unit is off or in a mode that does not allow them.

## How data is updated

| Data | How | When |
|---|---|---|
| State of the unit | Pushed by the Toshiba cloud | When it changes |
| State of the unit, full refresh | Fetched by the library | Every 30 minutes |
| Energy consumption | Fetched by the library | Every hour |

A stored access token avoids a new login at each restart, because Toshiba limits logins strictly. If the connection is lost and does not come back within 30 seconds, the integration reloads by itself.

## Actions

| Action | Description |
|---|---|
| `toshiba_ac_link.reconnect` | Makes a new connection to the Toshiba cloud for all entries. Use it after closing another connection to the same account, or to pick up a new unit |

All other control goes through the standard actions of the entities, such as `climate.set_temperature`.

## Automation examples

Turn on heating before you get home:

```yaml
automation:
  - alias: "Heat the living room on the way home"
    triggers:
      - trigger: state
        entity_id: person.me
        to: "home"
    actions:
      - action: climate.set_hvac_mode
        target:
          entity_id: climate.living_room
        data:
          hvac_mode: heat
      - action: climate.set_temperature
        target:
          entity_id: climate.living_room
        data:
          temperature: 21
```

Keep the outdoor unit quiet at night:

```yaml
automation:
  - alias: "Quiet outdoor unit at night"
    triggers:
      - trigger: time
        at: "22:30:00"
    actions:
      - action: select.select_option
        target:
          entity_id: select.living_room_outdoor_unit_silent_mode
        data:
          option: cdu_silent_1
```

## Supported devices

If your unit works with the [Toshiba Home AC Control](https://play.google.com/store/apps/details?id=com.toshibatctc.SmartAC) app, it has a good chance of working here. Users of the original project keep a [list of tested hardware](https://github.com/h4de5/home-assistant-toshiba_ac/issues/45).

> [!WARNING]
> **North American units do not work.** Toshiba sells them with a different app and system. [midea-ac-py](https://github.com/mill1000/midea-ac-py) may be able to control them.

## Known limitations

- **It needs the cloud.** No internet or a Toshiba outage means no control. There is no local mode.
- **One connection per account.** A second client takes the connection away from the first.
- **Toshiba limits requests.** Restarting Home Assistant repeatedly makes login problems worse.
- **New units need a reconnect.** A unit you add to the account appears after the `reconnect` action or a restart.
- **Removed units stay.** Delete a unit that you removed from the account by hand.
- **New units in the app only.** This integration cannot add units or change Wi-Fi settings.

## Troubleshooting

**"Toshiba refused the cloud connection", or "Credentials invalid".**
Your password is probably fine. Another client holds the single connection of the account: another Home Assistant, another Toshiba integration, or the app. Close it, then call `toshiba_ac_link.reconnect`.

**Setup fails after a restart.**
The Toshiba cloud may be unavailable, or may be limiting requests. Home Assistant keeps retrying by itself, so do not restart it again: wait an hour or two. Check that the app works in the meantime.

**The form says "Invalid username or password".**
Check that you can log in to the app. Some users report problems with long passwords or special characters. Avoid repeated attempts, because Toshiba counts failed logins toward an account lockout.

**Entities are unavailable just after setup.**
An outdoor temperature or a switch can stay unavailable while the unit is off, because it sends nothing. This is normal.

**The energy sensor shows unknown.**
It updates once an hour, and only on units that report consumption.

To collect details, add this to `configuration.yaml` and restart:

```yaml
logger:
  default: warning
  logs:
    custom_components.toshiba_ac_link: debug
    toshiba_ac_community: debug
```

Then [open an issue](https://github.com/anto294/homeassistant-toshiba-ac/issues), with the file from **Download diagnostics** in the menu of the entry. That file does not contain your account, password or tokens, but debug logs can, so check them before you share them. Problems in how the unit or the cloud behaves may belong to [the protocol library](https://github.com/vmvelev/Toshiba-AC-control/issues).

## Removal

1. In **Settings → Devices & services → Toshiba Link**, open the three-dot menu of the entry and select **Delete**. This also removes its devices and entities.
2. In **Settings → Marketplace**, open **Toshiba Link**, then three-dot menu → **Uninstall**, and confirm. After a manual installation, delete `config/custom_components/toshiba_ac_link/` instead.
3. Restart Home Assistant if a repair asks you to. After a manual installation, always restart it.

Remove automations, scripts and dashboards that use these entities yourself. Long-term statistics stay until you delete them in **Developer tools → Statistics**.

## Translations

The integration is available in English, German, Dutch and Italian. To add a language, copy `translations/en.json` to a new file named after the language code, and open a pull request. A test checks that each language has every text.

## Migrating from another Toshiba integration

Toshiba Link uses its own domain, so it installs next to `toshiba_ac` or `toshiba_ac_community` and does not take over their entries. Because of the single connection, do not run them on the same account at once:

1. Note the entity IDs that your automations and dashboards use.
2. Delete the entry of the old integration, so that its connection closes, and uninstall it.
3. Install Toshiba Link and add your account.
4. Check your automations and dashboards. Entity IDs are built from the name of each unit, so most stay the same, except these:
   - The energy sensor is now called `..._energy_consumption`. On the original integration it was `..._power_consumption`.
   - History stays with the old entities, because they belong to another integration.

## Differences from those projects

| | Toshiba Link | [h4de5](https://github.com/h4de5/home-assistant-toshiba_ac) | [vmvelev](https://github.com/vmvelev/home-assistant-toshiba_ac) |
|---|---|---|---|
| Domain | `toshiba_ac_link` | `toshiba_ac` | `toshiba_ac_community` |
| Protocol library | `toshiba-ac-community` | `toshiba-ac` | `toshiba-ac-community` |
| Reauthentication, stored access token, reload after a lost connection | Yes | No | Yes |
| Notice about the single connection | Yes | Yes | No |
| Indoor temperature, self cleaning, wireless LED, horizontal swing | Yes | No | Yes |
| Tests on a real Home Assistant, in CI | Yes | No | No |
| Minimum Home Assistant | 2026.11.0 | 2025.3.0 | 2025.3.0 |

## Documentation for developers

- [Architecture](docs/codebase-architecture.md)
- [Authentication](docs/authentication.md)

## Quality scale

Home Assistant grades the integrations in its core with the [Integration Quality Scale](https://contributors.home-assistant.io/core/quality-scale/). It does not review custom integrations, so Toshiba Link has no official tier. This project follows those rules as a goal, and this is its own assessment. [`quality_scale.yaml`](custom_components/toshiba_ac_link/quality_scale.yaml) holds the state of each rule.

| Tier | Rules | Met | Not applicable | Not met |
|---|---:|---:|---:|---:|
| Bronze | 20 | 17 | 3 | 0 |
| Silver | 10 | 8 | 1 | 1 |
| Gold | 21 | 13 | 2 | 6 |
| Platinum | 3 | 2 | 0 | 1 |

A tier needs every rule of that tier and the ones below it, so this project meets Bronze, and does not claim the others yet.

## Credits and license

This project started as a fork of [h4de5/home-assistant-toshiba_ac](https://github.com/h4de5/home-assistant-toshiba_ac), created by h4de5 and its contributors, and includes work from [vmvelev/home-assistant-toshiba_ac](https://github.com/vmvelev/home-assistant-toshiba_ac) by Venelin Velev. The communication with the cloud is done by [toshiba-ac-community](https://github.com/vmvelev/Toshiba-AC-control), a fork of [KaSroka/Toshiba-AC-control](https://github.com/KaSroka/Toshiba-AC-control). Thanks to all of them.

Distributed under the [GNU General Public License v3.0](LICENSE), like the projects it comes from.
