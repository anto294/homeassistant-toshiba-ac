# CLAUDE.md

Home Assistant custom integration (Marketplace, formerly HACS) for Toshiba air conditioners, named "Toshiba Link". Domain: `toshiba_ac_link`. All code lives in `custom_components/toshiba_ac_link/`. Repository: `anto294/homeassistant-toshiba-ac`, which started as a fork of `h4de5/home-assistant-toshiba_ac` (domain `toshiba_ac`), includes work from `vmvelev/home-assistant-toshiba_ac` (domain `toshiba_ac_community`), and is distributed separately. Repository links (manifest, README, release scripts) must point to this repository, not to those. Both are GPL-3.0, so this project stays GPL-3.0, and code taken from them needs its origin in the commit message.

Everything in the repository is in English: code, comments, docs, commits and pull requests. The `translations/` files are the only exception.

## Toshiba protocol

All communication is in `custom_components/toshiba_ac_link/toshiba/`, a native client on `aiohttp` with no other dependency. The manifest has no requirements. `properties.py`, `features.py` and `fcu_state.py` came from `toshiba-ac-community` under Apache-2.0, and `device.py` is based on it, so keep their notices and list changes in `toshiba/NOTICE`.

See `docs/authentication.md` for the requests and limits, and `docs/codebase-architecture.md` for the transports and for what was measured. Keep both in step with the code.

- **The first goal: never harm an account.** Toshiba's WAF limits requests, failed logins count toward a lockout, and the real limits are unknown, so never promise that a ban cannot happen. Every limit lives on disk in `guard.py`, so a restart cannot reset it. Rules that have no exceptions: only `config_flow.py` logs in, the password is never stored, the client retries no request by itself (the core retries a failed setup, and the guard still applies), every request goes through `ToshibaGuard`, and a new request needs a test in `tests/test_account_safety.py`, which counts requests across restarts.
- **Cloud only.** HTTPS (`mobileapi.toshibahomeaccontrols.com`) for requests and commands, and an Azure Web PubSub WebSocket for pushes. The units offer nothing on the local network.
- **Pushes can be late and are irregular.** Changes made in the app arrived 90 to 100 seconds late in the measurements, commands from this client were echoed within a second, and heartbeats are far apart. So a command updates the state as soon as Toshiba accepts it, a missing echo leads to a warning and a list request but never to a repeat, and heartbeats never decide availability.
- **Commands.** `POST /api/IoTHub/SendCommand` works on a real unit of model 2. It was found through `christoh/FroniusMonitor`, which is AGPL-3.0: take protocol facts from it, never code. Do not send a command to a real unit without the user's explicit OK.
- **The app is off limits as a source of secrets.** Do not decrypt its `ApplicationConfig.dll` or bypass its SSL pinning. `apk/` is ignored by git and is never published.
- **North American units** use a different system and cannot work here.

## Architecture

- **`__init__.py`**: `async_setup` registers the `reconnect` action, so it exists even when no entry loaded. `async_setup_entry` builds the `ToshibaAcDeviceManager`, connects, and forwards to the platforms. `entry.runtime_data` holds the manager (`ToshibaConfigEntry`). `ToshibaAuthError` becomes `ConfigEntryAuthFailed`, `ToshibaBlockedError` becomes `ConfigEntryNotReady` with `retry_after`, since Home Assistant alone would retry within 10 minutes, and every other `ToshibaError` a plain `ConfigEntryNotReady`. All of them, and every `HomeAssistantError` that an action raises, carry a translation key from `exceptions` in `strings.json`, through `translation_of` in `toshiba/errors.py` for an error of the client. Those errors stay in English.
- **`config_flow.py`**: calls `async_login` once per form. The entry holds the token, not the password. The unique id is the consumer id, so an account can only be added once. Reauthentication passes the stored `device_id`. Forms use `probatio`, which replaced voluptuous in the types of Home Assistant 2026.11.
- **`toshiba/`**: `guard.py` (limits and reusable answers, saved before each request), `api.py` (one request per call), `realtime.py` (the socket, reconnecting with growing waits), `manager.py` (the account: units, channel, periodic requests), `device.py` (one unit, the interface entities use).
- **Entities**: `entity.py` has `ToshibaAcEntity` (device info, availability from `device.connected`) and `ToshibaAcStateEntity`, which subscribes to `on_state_changed_callback`, which also fires when the connection changes. `should_poll` is off, because updates are pushed. `select.py` and `switch.py` are declarative: frozen `*Description` dataclasses with `ac_attr_name` (the device attribute), and `set_<ac_attr_name>` as the default setter (`entity_description.py`). Entities exist only for what a unit has: from `device.supported`, or, for the wireless LED and self cleaning, from the reported state, where `NONE` means no such function. Availability can also depend on the mode, through `supported.for_ac_mode()`.
- **`climate.py`**: uses the `native_*` temperature members (the others stop working in 2027.11). `is_on` is False during self cleaning, because the unit reports ON while its fan dries it. Temperature limits are in `min_temp` and `max_temp`, which depend on the 8 °C mode and on the model. Horizontal swing depends on `supports_independent_swing`, which can turn true after startup, so `supported_features` is dynamic. On those units, a vertical preset must go through `set_ac_vertical_swing_mode`, because the legacy value also means "horizontal none".
- **Names and icons**: an entity's name comes from `translation_key`, through `entity` in `strings.json` and `translations/*.json`. Icons are in `icons.json`, by `translation_key`, including `state` icons for off. Entity IDs come from the unit's name and the entity's name in the language of Home Assistant when the entity is first created, and unique IDs are `<ac_unique_id>_<key>`. Don't change either, because existing users' entities would break. The energy sensor keeps the historical suffix `_sensor` in its unique ID for the same reason.
- **Diagnostics**: `TO_REDACT` in `diagnostics.py` holds what must not reach a public issue, including `title` and `unique_id`, since these hold the account. Add any new sensitive field there. Don't log the username, password, tokens or the socket address, even at debug level.
- **Quality scale**: `quality_scale.yaml` holds the state of each rule, up to Platinum. Tiers are assigned to integrations in core, so never claim an official one. The README's "Quality scale" section counts the rules, so keep it in step.

## Adding a feature (typical flow)

1. Check that the member exists on `ToshibaAcDevice` or `ToshibaAcFeatures` in `toshiba/`.
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
- `manifest.json` has no requirements. Keep it that way if you can: `aiohttp` comes with Home Assistant.
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
python -m pytest --cov=custom_components.toshiba_ac_link --cov-report=term-missing
```

Tests live in `tests/` (pytest, `[tool:pytest]` in `setup.cfg`) and run against an installed Home Assistant. `tests/conftest.py` starts a real core with `run_in_hass`, and `async_add_entry` adds an entry through the real form. `make_device` and `make_manager` replace the client's objects for tests of entities, with its real enums. `tests/fake_toshiba.py` is a local server for tests of the client itself, which counts requests. No test talks to Toshiba. Mock a synchronous Home Assistant API with `MagicMock`, not `AsyncMock`, which would hide an `await` of a callback. CI runs the tests in the "Import and test" job on the oldest supported and the latest Home Assistant, and also runs hassfest.

To check changes by hand, copy `custom_components/toshiba_ac_link` into the `config/custom_components/` directory of a Home Assistant dev instance, and turn on debug logging for `custom_components.toshiba_ac_link`.
