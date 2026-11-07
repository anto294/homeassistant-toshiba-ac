# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

The first version of Toshiba Link. It is a separate integration with its own
`toshiba_ac_link` domain, which started from version 2026.10.0 of
[h4de5/home-assistant-toshiba_ac](https://github.com/h4de5/home-assistant-toshiba_ac)
and includes work from version 2026.10.0 of
[vmvelev/home-assistant-toshiba_ac](https://github.com/vmvelev/home-assistant-toshiba_ac).

### Before you upgrade

- **It does not take over an existing installation.** Remove the old integration
  first, because Toshiba allows one connection per account. See "Migrating from another
  Toshiba integration" in the README.
- **It needs Home Assistant 2026.11.0 or later.**

### Added

- A reauthentication step: when the password stops working, Home Assistant asks for the
  new one.
- Indoor temperature sensor, self cleaning binary sensor, and wireless LED switch.
- Horizontal swing on units with independent vanes.
- The integration reloads by itself when the cloud connection is lost and does not come
  back within 30 seconds.
- Italian translation, icons in `icons.json`, and the brand images inside the integration.

### Changed

- The protocol library is now `toshiba-ac-community` instead of `toshiba-ac`. It spaces
  out its requests and retries them, and sends a stable Device-ID.
- A restart no longer needs a new login, because the access token is stored. A new SAS
  token is registered at each start instead of reusing a stored one.
- Only a rejected login now asks for the password. WAF and rate limit responses keep
  retrying.
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
