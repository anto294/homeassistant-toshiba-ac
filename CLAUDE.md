# CLAUDE.md

Home Assistant custom integration (Marketplace, formerly HACS) for Toshiba air conditioners, named "Toshiba Link". Domain: `toshiba_ac_link`. All code lives in `custom_components/toshiba_ac_link/`. Repository: `anto294/homeassistant-toshiba-ac`, which started as a fork of `h4de5/home-assistant-toshiba_ac` (domain `toshiba_ac`), includes work from `vmvelev/home-assistant-toshiba_ac` (domain `toshiba_ac_community`), and is distributed separately. Repository links (manifest, README, release scripts) must point to this repository, not to those. Both are GPL-3.0, so this project stays GPL-3.0, and code taken from them needs its origin in the commit message.

Everything in the repository is in English: code, comments, docs, commits and pull requests. The `translations/` files are the only exception.

## Toshiba protocol

All communication is in the `toshiba-ac-community` library (`vmvelev/Toshiba-AC-control`), imported as `toshiba_ac_community`. This repository only maps it onto Home Assistant. Read the installed library before using one of its members, because it changes between releases. Import names from the module that defines them (`toshiba_ac_community.device.properties`, `.device.features`), since `toshiba_ac_community.device` does not export them for strict mypy. Problems in how a unit or the cloud behaves belong in the library.

See `docs/authentication.md` for tokens and endpoints, and `docs/codebase-architecture.md` for the transports. Keep both in step with the code.

- **Cloud only.** The units talk to Toshiba's REST API (`mobileapi.toshibahomeaccontrols.com`) and to Azure IoT Hub, and offer nothing on the local network. All writes go over IoT Hub, and reads arrive as pushes. The library also fetches each unit's state every 30 minutes, and energy every hour.
- **One connection per account.** A second client (another Home Assistant, another Toshiba integration, the app) makes IoT Hub refuse the connection, with the text `Credentials invalid`, which reads like a wrong password but is not one. `__init__.py` translates this for commands (a patch of `ToshibaAcAmqpApi.send_message` that calls the original, because the original sets message headers that commands need) and for setup (a persistent notification, dismissed by the next successful setup). A test that opens real connections, or any manual test on an account in use, disconnects that account's other client.
- **Rate limits and lockout.** Toshiba's WAF limits requests, `POST /api/Consumer/Login` above all, and failed logins count toward an account lockout. Never add a retry around a login or a loop that can repeat one. That is why the access token, its type and the consumer id are in `entry.data`.
- **North American units** use a different system and cannot work here.

## Architecture

- **`__init__.py`**: `async_setup` registers the `reconnect` action, so it exists even when no entry loaded. `async_setup_entry` builds the `ToshibaAcDeviceManager` with `sas_token=None` and the stored access token, connects within `CONNECT_TIMEOUT_S`, fetches the devices once so that a slow answer ends in a retry, and forwards to the platforms. `entry.runtime_data` holds the device manager (`ToshibaConfigEntry`). `_setup_error` classifies failures: only `ToshibaAcHttpApiAuthError` becomes `ConfigEntryAuthFailed`. Do not go back to looking for words in the error text, which turned WAF responses into a request for the password. Everything else is `ConfigEntryNotReady`, so Home Assistant retries.
- **`reconnect.py`**: `ToshibaReconnectManager` watches the Azure client's `on_connection_state_change`, which arrives on another thread. After a loss it reloads the entry when `RELOAD_DELAY_S` has passed. A new SAS token means that the client reconnected by itself, which cancels the reload and attaches to the new client. A call from a replaced client is ignored. `stop()` runs on unload.
- **`config_flow.py`**: `async_validate_input` connects with a new device id, reads the tokens from `http_api` **before** `shutdown()`, which clears it, and returns the entry's data. The unique id is the consumer id, so an account can only be added once. Reauthentication passes the stored `device_id`, so it does not register another device with Toshiba. Forms use `probatio`, which replaced voluptuous in the types of Home Assistant 2026.11.
- **Entities**: `entity.py` has `ToshibaAcEntity` (device info, availability) and `ToshibaAcStateEntity`, which subscribes to `on_state_changed_callback`. `should_poll` is off, because updates are pushed. `select.py` and `switch.py` are declarative: frozen `*Description` dataclasses with `ac_attr_name` (the device attribute), and `set_<ac_attr_name>` as the default setter (`entity_description.py`). Entities exist only for what a unit has: from `device.supported`, or, for the wireless LED and self cleaning, from the reported state, where `NONE` means no such function. Availability can also depend on the mode, through `supported.for_ac_mode()`.
- **`climate.py`**: uses the `native_*` temperature members (the others stop working in 2027.11). `is_on` is False during self cleaning, because the unit reports ON while its fan dries it. Temperature limits are in `min_temp` and `max_temp`, which depend on the 8 °C mode and on the model. Horizontal swing depends on `supports_independent_swing`, which can turn true after startup, so `supported_features` is dynamic. On those units, a vertical preset must go through `set_ac_vertical_swing_mode`, because the legacy value also means "horizontal none".
- **Names and icons**: an entity's name comes from `translation_key`, through `entity` in `strings.json` and `translations/*.json`. Icons are in `icons.json`, by `translation_key`, including `state` icons for off. Entity IDs come from the unit's name and the English name, and unique IDs are `<ac_unique_id>_<key>`. Don't change either, because existing users' entities would break. The energy sensor keeps the historical suffix `_sensor` in its unique ID for the same reason.
- **Diagnostics**: `TO_REDACT` in `diagnostics.py` holds what must not reach a public issue, including `title` and `unique_id`, since these hold the account. Add any new sensitive field there. Don't log the username, password or tokens, even at debug level.
- **Quality scale**: `quality_scale.yaml` holds the state of each rule, up to Platinum. Tiers are assigned to integrations in core, so never claim an official one. The README's "Quality scale" section counts the rules, so keep it in step.

## Adding a feature (typical flow)

1. Read the member in the installed `toshiba_ac_community` library, and check that it exists on `ToshibaAcDevice` or `ToshibaAcFeatures`.
2. Add a description to `_SWITCH_DESCRIPTIONS` or `_SELECT_DESCRIPTIONS`, or an entity class in the right platform. Make it exist only on units that have the function.
3. Add the `translation_key` to `strings.json` and every file in `translations/`, and to `icons.json`: `tests/test_translations.py` fails if a language lacks a key or a placeholder.
4. Add tests through real actions, as in `tests/test_entities.py`.
5. Update the entity table in the README and the platforms in `docs/codebase-architecture.md`.

## Conventions

- One logical change per commit, with a detailed English message (summary, then what and why). Never batch unrelated changes. Run black before committing.
- Format with **black** (CI runs `black . --check`). Run `black .` before committing.
- Types: CI runs `mypy -p custom_components.toshiba_ac_link`, strict, configured in `setup.cfg`. A `type: ignore` needs a comment that says why.
- Lint: CI runs `flake8` with `max-line-length=127`. `setup.cfg` sets 220 for local use.
- Security: CI runs Bandit on the integration, and CodeQL runs separately.
- Every function and class gets a docstring, and top-level ones get this banner:
  ```python
  # ---------------------------
  #   function_name
  # ---------------------------
  ```
- Never block the event loop.
- `manifest.json` pins `azure-iot-device`, `toshiba-ac-community` and `janus`, and the `Pipfile` pins the same versions. Keep `azure-iot-device` first, since Home Assistant installs in that order and the library needs that version. It is a release candidate because the library requires exactly that one.
- `manifest.json` keeps `"version": "0.0.0"`. The release workflow (`.github/update_version.py`) sets the real version.
- The Marketplace, which is HACS built into Home Assistant 2026.11 and which reads the same `hacs/default` catalog and `hacs.json`, installs from the `toshiba_ac_link.zip` asset that `release.yml` attaches to each published GitHub release (`zip_release` in `hacs.json`). Publish a full GitHub release, not just a tag; mark betas as pre-releases. Tags are `vX.Y.Z` or `vX.Y.ZbN` (the release workflow rejects anything else). Every user-facing change gets an entry under `## [Unreleased]` in `CHANGELOG.md` (Keep a Changelog format); the release notes are that version's section, passed with `gh release create --notes-file`. README.md is the page the Marketplace shows.
- Minimum HA version: 2026.11.0, Python 3.14. See `hacs.json` (`homeassistant`) and `Pipfile`. Keep `hacs.json` at the oldest core that has every HA API the code uses. Do not lower it, and do not add code for older cores.
- The default branch is `main`. The `upstream` remote is `h4de5/home-assistant-toshiba_ac`, and `vmvelev` is the other source. Both are for reading and for porting single changes, never for merging: domains and files differ.

## Development commands

```bash
pipenv install --dev            # or: python .github/generate_requirements.py && pip install -r requirements_tests.txt
black .
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
mypy -p custom_components.toshiba_ac_link
python -m pytest
```

Tests live in `tests/` (pytest, `[tool:pytest]` in `setup.cfg`) and run against an installed Home Assistant. `tests/conftest.py` starts a real core with `run_in_hass`, and `async_add_entry` adds an entry through the real form. `make_device` and `make_manager` replace only the library's objects, with its real enums, so no test talks to Toshiba. Mock a synchronous Home Assistant API with `MagicMock`, not `AsyncMock`, which would hide an `await` of a callback. CI runs the tests in the "Import and test" job on the oldest supported and the latest Home Assistant, and also runs hassfest.

To check changes by hand, copy `custom_components/toshiba_ac_link` into the `config/custom_components/` directory of a Home Assistant dev instance, on a Toshiba account that nothing else is using, and don't restart it repeatedly. Turn on debug logging for `custom_components.toshiba_ac_link` and `toshiba_ac_community`.
