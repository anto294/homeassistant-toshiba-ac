# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
  per hour. A loop of restarts costs what one start costs.
- Italian translation, icons in `icons.json`, and the brand images inside the integration.

### Changed

- A native client replaces the protocol library and the Azure IoT SDK. It uses the same
  channel as the current Toshiba app, Azure Web PubSub, and HTTPS for commands.
- The password is no longer stored, and only the forms log in. When the token stops
  working, in about two years or after a password change, Home Assistant asks for the
  password.
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
