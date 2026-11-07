# Toshiba Link for Home Assistant

[![GitHub release](https://img.shields.io/github/v/release/anto294/homeassistant-toshiba-ac?include_prereleases&style=plastic)](https://github.com/anto294/homeassistant-toshiba-ac/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/anto294/homeassistant-toshiba-ac/ci.yml?style=plastic)](https://github.com/anto294/homeassistant-toshiba-ac/actions)
![Downloads](https://img.shields.io/github/downloads/anto294/homeassistant-toshiba-ac/total?style=plastic)

This integration lets you monitor and control Toshiba air conditioners with a Wi-Fi module or adapter from Home Assistant: mode, temperature, fan, vanes, power presets, comfort functions, temperatures and energy use. It uses the same cloud service and the same realtime channel as the current Toshiba app, with its own client and no other dependencies, and it is built so that restarts of Home Assistant cannot harm your Toshiba account.

> [!IMPORTANT]
> **This is a beta.** Only pre-releases exist so far, so the Marketplace shows Toshiba Link only with beta versions turned on. See [Tested units](#tested-units) for what has been tried on real hardware.

> [!NOTE]
> Toshiba Link started as a fork of [h4de5/home-assistant-toshiba_ac](https://github.com/h4de5/home-assistant-toshiba_ac), and includes work from [vmvelev/home-assistant-toshiba_ac](https://github.com/vmvelev/home-assistant-toshiba_ac). It is a separate integration with its own `toshiba_ac_link` domain, so please report problems [here](https://github.com/anto294/homeassistant-toshiba-ac/issues), not to those projects. See [Differences from those projects](#differences-from-those-projects).

## Contents

- [What you can do with it](#what-you-can-do-with-it)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Entities](#entities)
- [How it works](#how-it-works)
- [Protecting your account](#protecting-your-account)
- [Your data](#your-data)
- [Actions](#actions)
- [Automation examples](#automation-examples)
- [Supported devices](#supported-devices)
- [Tested units](#tested-units)
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

Turn on beta versions for this repository, since only pre-releases exist so far, then select **Install**, confirm, and restart Home Assistant if a repair asks you to. Otherwise the installation can fail with a 404 error in the log.

### Manual

Download `toshiba_ac_link.zip` from the newest entry on the [releases page](https://github.com/anto294/homeassistant-toshiba-ac/releases), extract it into `config/custom_components/toshiba_ac_link/` and restart Home Assistant.

### Updating

The Marketplace offers a new version as an update under **Settings → Updates**. After you install it, restart Home Assistant. After a manual installation, replace the contents of the folder and restart.

## Configuration

Go to **Settings → Devices & services → Add integration** and search for **Toshiba Link**.

| Field | Description |
|---|---|
| Username | The account you use in the Toshiba app |
| Password | The password of that account |

Each account can be added once. There are no other options.

Your password is used for one login and is not stored. Toshiba's token lasts about two years. When it stops working, Home Assistant asks for the password under **Settings → Devices & services**.

The Toshiba app can stay in use at the same time. Enter the password with care, because the form allows 3 failed attempts per hour.

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
| 8 °C mode | Switch | Frost protection, with a target of 5 to 13 °C, or to 16 °C on model 3 units. Turning it on sets the target to 8 °C. Turning it off goes back to the target from before Home Assistant turned it on, or to 20 °C if that is not known, such as after a restart or when it was turned on in the app |
| Air purifier | Switch | |
| ECO mode | Switch | |
| High power mode | Switch | |
| Wireless LED | Switch | Configuration entity, also works while the unit is off |

Outside the 8 °C mode, the target temperature goes from 17 to 30 °C, in whole degrees. Except for the wireless LED, switches and selects are unavailable when they do not apply, such as while the unit is off or in a mode that does not allow them. So on a unit that is off, most of them are unavailable, which is normal. Which functions a unit has comes from the data that Toshiba reports for it.

The first part of an entity ID comes from the name of the unit in the app, and the rest from the name of the entity in the language of your Home Assistant. For example, the same switch can be `switch.living_room_eco_mode` or `switch.clima_soggiorno_modalita_eco`. Check yours under **Settings → Devices & services**, also when you copy the examples below.

## How it works

Your units talk only to the Toshiba cloud. Home Assistant talks to it in two ways: HTTPS for requests and commands, and one WebSocket on Azure Web PubSub, on which the units publish their changes. Nothing is installed on the units, and no local connection is used.

| Data | How | When |
|---|---|---|
| State of the unit | Pushed by the Toshiba cloud | When it changes. A change made in the app can take a minute or two |
| State of all units, full refresh | One request | Every 30 minutes, and 5 minutes after a reconnection or a restart that reused a recent list |
| Energy consumption | One request, only if a unit reports it | Every hour |
| A command | One request, never repeated automatically | When you send it. The entity shows the new value as soon as Toshiba accepts it |
| Login | One request | Only when you send the setup or reauthentication form |

If the WebSocket drops, the entities of that account become unavailable, and the integration reconnects by itself, waiting 5 seconds or more, and up to 15 minutes if it keeps failing.

## Protecting your account

Toshiba limits requests and locks accounts after failed logins, without saying where the limits are. This integration is built around that, although nothing can guarantee that Toshiba never blocks an account:

- It logs in only when you send a form, so a restart can never repeat a login.
- A restart reuses a recent list of units, so it costs one request, for opening the channel, or two after 15 minutes. Running on for 5 minutes adds one more, to correct the list.
- If Toshiba answers 403 or 429, all requests stop for at least 15 minutes, doubling up to a day, even across restarts. It does not ask for your password in that case.
- It stays below 120 requests per hour. Past that, requests and commands wait until the hour ends, so a restart loop cannot go on sending.
- A token that Toshiba rejected is never sent again, and Home Assistant asks for the password.
- The form stops after 3 failed logins in an hour on one account.

These limits are saved on disk before each request, so restarting does not reset them. Automated tests count the requests that restart loops make. Still, avoid other Toshiba integrations on the same account, since their requests add up and this integration cannot limit them.

## Your data

- **Your password** goes to Toshiba once, when you send a form, and is not stored.
- **The token and your username** are stored in Home Assistant's configuration, like those of other integrations. Whoever can read your configuration folder or backups can use the token, so treat them as sensitive. Whether changing your Toshiba password cancels a token has not been tested.
- **Limits and the last list of units** are in a private file under `.storage/`.
- **Everything goes only to Toshiba and its Azure service.** This project collects nothing.
- **Diagnostics** leave out your account, tokens, and the IDs and names of your units, so you can attach them to an issue.

## Actions

| Action | Description |
|---|---|
| `toshiba_ac_link.reconnect` | Makes a new connection to the Toshiba cloud for all entries. Use it to pick up a new unit, up to 15 minutes after adding it to the account |

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

## Tested units

Tested by the author on Home Assistant 2026.11, on two accounts:

| Unit | Toshiba model data | Tried so far |
|---|---|---|
| RAS-B10N4KVSG-E, 5 units, firmware 5.0.00 | Model 2, features `2c00` | Reading, turning on in a mode with one command, turning off, temperature, fan while off, 8 °C mode, entering and leaving it, with targets of 8 to 13 °C, silent mode, reconnection, restart |
| Unknown model, 2 units, firmware 5.0.00 | Model 3, features `2c03` | Reading, the energy answer, turning on in a mode with one command, turning off, and the 8 °C mode, entering and leaving it, where the unit keeps its target at 16 °C or below. With the client alone, not yet in Home Assistant |

Results with other units are welcome in the [issues](https://github.com/anto294/homeassistant-toshiba-ac/issues). The diagnostics file shows the model data of each unit. Not yet tried on a real unit: toggling during self cleaning, independent vanes, and a unit joining or leaving the account.

## Known limitations

- **It needs the cloud.** No internet or a Toshiba outage means no control. There is no local mode.
- **A command can get lost.** In our tests Toshiba accepted about 1 command in 15 that a unit then did not apply. The entity shows the command at once, and corrects itself after 2 minutes, with a warning in the log. It does not send the command again, so check that automations did what you meant if you see that warning.
- **Changes made in the app can arrive late.** They took a minute or two in our measurements. Commands from Home Assistant were confirmed in about a second.
- **Toshiba limits requests.** See [Protecting your account](#protecting-your-account).
- **New units can take a while.** A unit you add to the account appears by itself within about 30 minutes. The `reconnect` action can bring it sooner, if the last list of units is more than 15 minutes old.
- **Whole degrees only.** Toshiba's protocol has no half degrees.
- **Removed units take a while to go.** A unit you remove from the account disappears by itself when two lists of units in a row no longer show it, usually within an hour. To delete it sooner, call the `reconnect` action or restart, up to 15 minutes later, then select **Delete** on the page of that device. A unit that is still on the account cannot be deleted.
- **New units in the app only.** This integration cannot add units or change Wi-Fi settings.

## Troubleshooting

**The Marketplace log says "Got status code 404" for `toshiba_ac_link.zip`.**
Only pre-releases exist so far, so turn on beta versions for Toshiba Link and install again.

**Home Assistant asks for the password.**
Toshiba rejected the token, or the token expired. Send the current password once. Nothing is retried until you do.

**Setup keeps retrying after a restart.**
The Toshiba cloud may be unavailable, or may have asked to slow down, and then the integration waits by itself for 15 minutes or more. Restarting does not shorten this. Check that the app works in the meantime. **Download diagnostics** shows how long the wait is, under `blocked_for_s`.

**The form says "Too many attempts".**
Wait for an hour. This protects your account, because Toshiba counts failed logins toward a lockout.

**The form says "Invalid username or password".**
Check that you can log in to the app. Some users report problems with long passwords or special characters. The form allows 3 failed attempts per hour.

**Entities are unavailable just after setup.**
An outdoor temperature or a switch can stay unavailable while the unit is off, because it sends nothing. This is normal.

**All entities of the account are unavailable.**
The realtime channel is down, and it reconnects by itself. Check your internet connection and whether the app works. `connected` in **Download diagnostics** shows its state. Commands may still work meanwhile, but the values could be out of date.

**The energy sensor shows unknown.**
It updates once an hour, and only on units that report consumption.

To collect details, add this to `configuration.yaml` and restart:

```yaml
logger:
  default: warning
  logs:
    custom_components.toshiba_ac_link: debug
```

Then [open an issue](https://github.com/anto294/homeassistant-toshiba-ac/issues), with the file from **Download diagnostics** in the menu of the entry. That file does not contain your account, password or tokens, but debug logs can, so check them before you share them.

## Removal

1. In **Settings → Devices & services → Toshiba Link**, open the three-dot menu of the entry and select **Delete**. This also removes its devices and entities.
2. In **Settings → Marketplace**, open **Toshiba Link**, then three-dot menu → **Uninstall**, and confirm. After a manual installation, delete `config/custom_components/toshiba_ac_link/` instead.
3. Restart Home Assistant if a repair asks you to. After a manual installation, always restart it.

Remove automations, scripts and dashboards that use these entities yourself. Long-term statistics stay until you delete them in **Developer tools → Statistics**.

## Translations

The integration is available in English, German, Dutch and Italian. To add a language, copy `translations/en.json` to a new file named after the language code, and open a pull request. A test checks that each language has every text.

## Migrating from another Toshiba integration

Toshiba Link uses its own domain, so it installs next to `toshiba_ac` or `toshiba_ac_community` and does not take over their entries. Do not run them on the same account at once, because they add requests and logins that this integration cannot limit:

1. Note the entity IDs that your automations and dashboards use.
2. Delete the entry of the old integration, and uninstall it.
3. Install Toshiba Link and add your account.
4. Check your automations and dashboards. Entity IDs are built from the name of each unit and the language of Home Assistant, so most stay the same, except these:
   - The energy sensor is now called `..._energy_consumption`. On the original integration it was `..._power_consumption`.
   - History stays with the old entities, because they belong to another integration.

## Differences from those projects

| | Toshiba Link | [h4de5](https://github.com/h4de5/home-assistant-toshiba_ac) | [vmvelev](https://github.com/vmvelev/home-assistant-toshiba_ac) |
|---|---|---|---|
| Domain | `toshiba_ac_link` | `toshiba_ac` | `toshiba_ac_community` |
| Connection to Toshiba | Own client, on the channel of the current app (Azure Web PubSub) | `toshiba-ac` library, on IoT Hub | `toshiba-ac-community` library, on IoT Hub |
| Limits that survive restarts, no stored password | Yes | No | No |
| Reauthentication | Yes | No | Yes |
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
| Silver | 10 | 9 | 1 | 0 |
| Gold | 21 | 15 | 3 | 3 |
| Platinum | 3 | 3 | 0 | 0 |

A tier needs every rule of that tier and the ones below it, so this project meets Bronze and Silver, and does not claim the others yet. The tests cover 99 percent of the code, and CI measures this on each run.

## Credits and license

Toshiba Link exists thanks to the work of these people and projects:

| Project | By | What this project owes it |
|---|---|---|
| [h4de5/home-assistant-toshiba_ac](https://github.com/h4de5/home-assistant-toshiba_ac) | h4de5 and [its contributors](https://github.com/h4de5/home-assistant-toshiba_ac/graphs/contributors), including Thibaut Etienne, Julie Koubova, Kamil Sroka, Andreas Pauschenwein, albaintor, Tom Lagodzinski and Sebastiaan Dammann | The original integration, which this project started as a fork of: its entities, forms and translations. GPL-3.0 |
| [vmvelev/home-assistant-toshiba_ac](https://github.com/vmvelev/home-assistant-toshiba_ac) | Venelin Velev | Reauthentication, stored tokens, indoor temperature, self cleaning, wireless LED, horizontal swing and many fixes. GPL-3.0 |
| [KaSroka/Toshiba-AC-control](https://github.com/KaSroka/Toshiba-AC-control) | Kamil Sroka | The original library, and the understanding of the protocol that all of these projects rest on. Apache-2.0 |
| [vmvelev/Toshiba-AC-control](https://github.com/vmvelev/Toshiba-AC-control) (`toshiba-ac-community`) | Venelin Velev | Its fork of that library. The decoding of the state and features of a unit in [`toshiba/`](custom_components/toshiba_ac_link/toshiba/) comes from it, and `device.py` is based on it. Apache-2.0, see [`NOTICE`](custom_components/toshiba_ac_link/toshiba/NOTICE) |
| [christoh/FroniusMonitor](https://github.com/christoh/FroniusMonitor) | christoh | Its public code shows how the current Toshiba channel works: the endpoints and message fields of Web PubSub and of commands. Only these facts were used, none of its code, which is under AGPL-3.0 |

Thank you all. Thanks also to everyone who reported problems and tested units in those projects.

Toshiba Link is distributed under the [GNU General Public License v3.0](LICENSE), like the integrations it comes from. The files that come from the Apache-2.0 library keep their [license](custom_components/toshiba_ac_link/toshiba/LICENSE) and notices.

This is an independent project, not affiliated with or endorsed by Toshiba. Toshiba and other names are trademarks of their owners.
