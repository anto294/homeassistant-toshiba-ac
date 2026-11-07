# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- The migration guide no longer says that most entity IDs stay the same. It now explains
  that they can change, also by starting with the area of the unit, and that device actions
  in scripts and automations must point to the new device.

## [0.1.1] - 2026-11-07

### Added

- A **Reconfigure** option in the menu of an entry corrects the username and password of
  the same account, without removing it. It logs in once for each time the form is sent,
  and refuses a different account without changing anything.

### Changed

- Errors that Home Assistant shows, from actions and on the integration page, are now in
  the language of the user, in English, German, Italian and Dutch. A wait after Toshiba
  asks to slow down is shown in minutes.
- The indoor temperature sensor now starts disabled, because the climate entity shows the
  same value. This only affects new installations and units added later: a sensor you
  already have stays enabled, with the same entity ID. To use it on a new unit, open the
  entity from the device page, and enable it in its settings.

## [0.1.0] - 2026-11-07

The first stable version of Toshiba Link. It is a separate integration with its own
`toshiba_ac_link` domain, which started from version 2026.10.0 of
[h4de5/home-assistant-toshiba_ac](https://github.com/h4de5/home-assistant-toshiba_ac)
and includes work from version 2026.10.0 of
[vmvelev/home-assistant-toshiba_ac](https://github.com/vmvelev/home-assistant-toshiba_ac).

### If you come from a beta

The code is the same as 0.1.0b5, so this update changes nothing in how the integration
works, and your entities and settings stay as they are. Beta versions no longer need to be
turned on. The changelog lists the changes of each beta.

### If you install it for the first time

- **It does not take over an existing installation.** Remove the old integration first, so
  that its requests and logins stop. See "Migrating from another Toshiba integration" in
  the README.
- **It needs Home Assistant 2026.11.0 or later.**
- **It is not in the Marketplace catalog yet.** Open the repository directly, with the
  button in the README.
- **Not everything was tried on real units.** See "Tested units" in the README, and "Known
  limitations" for what to expect from the Toshiba cloud.

### What you get compared to the projects it started from

- A native client replaces the protocol library and the Azure IoT SDK. It uses the same
  channel as the current Toshiba app, Azure Web PubSub, and HTTPS for commands.
- Limits that protect the account and survive restarts: at most 120 requests per hour, a
  wait of 15 minutes or more when Toshiba asks to slow down, and at most 3 failed logins per
  hour. A restart costs one request, or two after 15 minutes. The real limits of Toshiba are
  unknown, so these reduce the risk to an account, and cannot rule it out.
- The password is no longer stored, and only the forms log in. Home Assistant asks for the
  password only when Toshiba rejects the token or the token expires.
- A command shows in Home Assistant as soon as Toshiba accepts it, and is never sent twice.
  If a unit does not confirm it within 2 minutes, a warning is logged and the entity
  corrects itself.
- Units that join or leave the account appear and disappear by themselves.
- The 8 °C mode sets a target of 8 °C when it starts, and goes back to the earlier target,
  or to 20 °C, when it ends. Model 3 units accept up to 16 °C in this mode.
- Turning off a switch or choosing off on a select no longer ends another function that
  shares its setting on the unit.
- Indoor temperature sensor, self cleaning binary sensor, wireless LED switch, and
  horizontal swing on units with independent vanes.
- Unexpected data from a unit or from Toshiba no longer stops the account or the realtime
  channel.
- The diagnostics file no longer holds the account name or the names of the units.

## [0.1.0b5] - 2026-11-07

### Changed

- The description of the `reconnect` action no longer suggests that it only works within
  15 minutes of adding a unit. A new unit appears by itself, and the action brings it
  sooner if the last list of units is more than 15 minutes old.
- The `reconnect` action now fails with a clear error if an entry does not load, so an
  automation can tell. It reloads all entries first, and makes no extra request.

### Fixed

- Choosing off on the outdoor unit silent mode or the fireplace mode no longer ends
  another function. These selects show off while ECO, high power or the 8 °C mode is
  active, and choosing off used to clear that function, which for the 8 °C mode also sent
  a target of 20 °C. It now sends no command unless the select's own function is active.
- Turning off the 8 °C mode no longer brings back a target from an earlier time. The saved
  target now lasts for one visit to the mode, so if the mode was turned on in the app,
  turning it off goes to 20 °C. A command that fails keeps the saved target for the next
  attempt.
- A unit reporting a mode outside its features can be turned on and off again. Every
  command used to fail with a raw error. A command that asks for a mode or status the unit
  lacks now fails with a clear message, and sends nothing.
- A command that Toshiba accepted no longer fails in Home Assistant when the answer has an
  unexpected shape. The action used to end with a raw error, and the entity did not
  update, although the command had reached Toshiba.
- An unknown value from a unit is now logged once per run, instead of several times for
  each message that contains it.

## [0.1.0b4] - 2026-11-07

### Fixed

- Turning off the 8 °C mode no longer leaves a target of 29 °C or more, at which a unit
  could really heat. It now goes back to the earlier target, or to 20 °C if that is not
  known. Turning the mode on now sets a target of 8 °C, instead of showing 2 °C or 6 °C.
- When a unit does not confirm a command within 2 minutes, a warning is logged and the
  entity corrects itself. Toshiba can accept a command that the unit never applies, and
  the entity used to show it until the next refresh, up to 30 minutes later.

## [0.1.0b3] - 2026-11-07

### Added

- A unit added to the account now appears by itself within about 30 minutes, without a
  restart. The same goes for a unit that was skipped because its data could not be read.
- A unit removed from the account now disappears by itself, once two lists of units in a
  row no longer show it. An empty list, or one with a unit that cannot be read, removes
  nothing.

### Changed

- Setting the fan mode no longer turns on a unit that is off. The setting applies when the
  unit next runs.
- Choosing a mode on a unit that is off now sends one command instead of two.

### Fixed

- A mode byte the integration does not know no longer removes the selects of all units at
  startup, or freezes the entities and fails commands when a unit pushes it. The climate
  entity shows unknown until a known mode arrives.
- After a restart or a reload within 15 minutes, an entity could show its older state for
  about half an hour. The integration now corrects the state 5 minutes after such a start.
- A unit with data that cannot be read is now skipped with a warning, and no longer stops
  the whole account. If no unit can be read, setup retries. Unexpected answers no longer
  end the periodic requests or the realtime channel for good.
- The energy sensor now becomes unavailable and available again with the connection, like
  the other entities.
- climate.set_temperature now applies the hvac_mode it is given, which it used to ignore.
- Losing and regaining the connection to the Toshiba cloud is now logged once each, at
  info level.
- Toggling a unit that is cleaning itself now turns it on, since the entity shows it as
  off. It used to send another off.
- Turning off a switch that is already off no longer clears another function. For example,
  turning off ECO no longer ends outdoor unit silent mode.
- A unit removed from the account can now really be deleted from its device page. Home
  Assistant used to refuse this, although the README said to do it.
- Every refused login now counts toward the limit of 3 failed logins per hour, not only
  those that name a wrong password.
- The description of the reconnect action now says what it is for, and no longer mentions
  closing another connection.
- On units that report independent vanes, a fixed vertical position or a horizontal
  setting is no longer dropped from the command when the unit's data does not declare it.
- A restart now shows the commands and pushes from before it right away, because the saved
  list of units is corrected as they happen.

### Security

- The diagnostics file no longer contains the names of the units, which can hold personal
  details.

## [0.1.0b2] - 2026-11-07

### Fixed

- A restart no longer loses about 10 seconds and a request trying to reuse the address of
  the realtime channel, which works for one connection only.

## [0.1.0b1] - 2026-11-07

The first version of Toshiba Link. It is a separate integration with its own
`toshiba_ac_link` domain, which started from version 2026.10.0 of
[h4de5/home-assistant-toshiba_ac](https://github.com/h4de5/home-assistant-toshiba_ac)
and includes work from version 2026.10.0 of
[vmvelev/home-assistant-toshiba_ac](https://github.com/vmvelev/home-assistant-toshiba_ac).

### Before you upgrade

- **It does not take over an existing installation.** Remove the old integration
  first, so that its requests and logins stop. See "Migrating from another Toshiba
  integration" in the README.
- **It needs Home Assistant 2026.11.0 or later.**

### Added

- A reauthentication step: when the token stops working, Home Assistant asks for the
  password.
- Indoor temperature sensor, self cleaning binary sensor, and wireless LED switch.
- Horizontal swing on units with independent vanes.
- Limits that protect the account and survive restarts: at most 120 requests per hour,
  a wait of 15 minutes or more when Toshiba asks to slow down, and at most 3 failed logins
  per hour. A restart costs one request, or two after 15 minutes.
- Italian translation, icons in `icons.json`, and the brand images inside the integration.

### Changed

- A native client replaces the protocol library and the Azure IoT SDK. It uses the same
  channel as the current Toshiba app, Azure Web PubSub, and HTTPS for commands.
- The password is no longer stored, and only the forms log in. When the token stops
  working, which should take about two years, Home Assistant asks for the password.
- Only a rejected token now asks for the password. WAF and rate limit responses wait.
- A command now shows in Home Assistant as soon as Toshiba accepts it.
- An account can only be added once.
- A self cleaning unit now shows as off, as it does in the Toshiba app.
- In the 8 °C mode, model 3 units now accept up to 16 °C.
- The power consumption sensor is now called Energy consumption, with state class
  `total`.
- The device page now shows the real model of the unit.

### Fixed

- Setting a vertical swing no longer resets the horizontal vane on units with independent
  vanes.
- The energy sensor no longer stays unknown for up to an hour after a start.
- ECO mode and high power mode now show an icon.
- The climate entity no longer uses members that Home Assistant removes in 2027.11.
- "Toshiba connection OK" is no longer logged as an error on every setup attempt.

### Security

- The diagnostics file no longer contains the account name in the title of the entry.
- The debug log no longer contains the account name and device id from the setup form.
- The password fields of the forms now hide what you type.
