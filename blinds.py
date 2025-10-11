# -*- coding: utf-8 -*-
import datetime
import time
import appdaemon.plugins.hass.hassapi as hass
import os, yaml, glob

from lib.blinds_lib import Blind, EVENING_HOUR_THRESHOLD, DEFAULT_EVENT_KILL_SWITCH_DURATION

class Blinds(hass.Hass):
    DEFAULT_TILT_DELAY = 85

    def initialize(self):
        """Main AppDaemon entrypoint for blinds control."""
        self.log("Initializing blinds...")

        # ───────────────────────────────
        # External app instances (kept from your version)
        # ───────────────────────────────
        self.max_temp_app = self.get_app("max_temp")
        self.sun_app = self.get_app("sun")

        # Wait for dependencies to report ready()
        for _trials in range(60):  # wait up to 30 seconds
            max_ready = getattr(self.max_temp_app, "ready", lambda: False)()
            sun_ready = getattr(self.sun_app, "ready", lambda: False)()
            if max_ready and sun_ready:
                break
            self.log(f"Waiting for dependencies... max_temp={max_ready}, sun={sun_ready}", level="DEBUG")
            time.sleep(0.5)
        if _trials + 1 == 60:
            self.log(f"Dependency wait loop raised. Continuing initialization.", level="WARNING")

        # ───────────────────────────────
        # Instantiate logic core
        # ───────────────────────────────
        self.b = Blind(**self.args["blind_config"])

        # ───────────────────────────────
        # Adaptive periodic evaluation setup
        # ───────────────────────────────
        self.TICK_INTERVAL = 180  # seconds between evaluations
        entity_id = self.args.get("entity_id", "")

        # ───────────────────────────────
        # Locate apps.yaml dynamically
        # ───────────────────────────────
        apps_yaml_paths = []

        # 1️⃣ Add-on install (hashed folder like /addon_configs/a0d7b954_appdaemon)
        addon_candidates = glob.glob("/addon_configs/*_appdaemon/apps.yaml")
        apps_yaml_paths.extend(addon_candidates)

        # 2️⃣ Typical Home Assistant Core or manual setup
        apps_yaml_paths += [
            "/config/appdaemon/apps.yaml",
            "/config/apps.yaml",
            "/config/apps/apps.yaml",
        ]

        # 3️⃣ Relative path (for developer/manual install)
        try:
            current_dir = os.path.dirname(__file__)
            parent_dir = os.path.abspath(os.path.join(current_dir, ".."))
            rel_path = os.path.join(parent_dir, "apps.yaml")
            apps_yaml_paths.append(rel_path)
        except Exception:
            pass

        # ───────────────────────────────
        # Parse the first valid file found
        # ───────────────────────────────
        total_blinds = 0
        for path in apps_yaml_paths:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        config = yaml.safe_load(f) or {}
                    for name, data in config.items():
                        if isinstance(data, dict) and data.get("module") == "blinds":
                            total_blinds += 1
                    self.log(f"[Init] Found {total_blinds} blinds in {path}")
                    break
                except Exception as e:
                    self.log(f"[WARN] Could not read {path}: {e}", level="WARNING")

        # ───────────────────────────────
        # Fallback if parsing failed
        # ───────────────────────────────
        if total_blinds == 0:
            total_blinds = int(self.args.get("total_blinds", 6))
            self.log(f"[WARN] Falling back to total_blinds={total_blinds}", level="WARNING")

        self.NUM_BLINDS = total_blinds

        # ───────────────────────────────
        # Evenly distribute tick offsets
        # ───────────────────────────────
        idx = abs(hash(entity_id)) % total_blinds
        offset = (idx / float(total_blinds)) * self.TICK_INTERVAL
        next_tick = datetime.datetime.now() + datetime.timedelta(seconds=offset)

        # First evaluation immediately
        self.tick(None)

        # Periodic evaluation every 3 min, staggered
        self.run_every(self.tick, next_tick, self.TICK_INTERVAL)

        # ───────────────────────────────
        # Nightly reset for max/min outside temp
        # ───────────────────────────────
        self.run_daily(self.set_max_outside_temp, datetime.time(1, 1, 3))

        # Ensure default sensor for yesterday's min temp
        if "min_temp_sensor_value_yesterday" not in self.args:
            self.args["min_temp_sensor_value_yesterday"] = (
                "input_number.yesterdays_min_outside_temp_over_24_hours"
            )

        # Initialize max/min outside temp now
        self.set_max_outside_temp(None)

        # ───────────────────────────────
        # Optional sensors & listeners
        # ───────────────────────────────
        if "contact" in self.args:
            try:
                self.b.SetDoorType()
            except Exception:
                pass
            self.listen_state(
                self.window_closed,
                entity_id=self.args["contact"],
                new="off",
                old="on",
            )

        if "wind_alarm" in self.args:
            self.listen_state(
                self.wind_alarm_off,
                entity_id=self.args["wind_alarm"],
                new="off",
                old="on",
            )

        if "dawn_lights" in self.args:
            for light in self.args["dawn_lights"]:
                self.listen_state(self.light_on, entity_id=light, new="on", old="off")
                self.listen_state(self.light_off, entity_id=light, new="off", old="on")

        # ───────────────────────────────
        # Event-based re-evaluations (new)
        # ───────────────────────────────
        self.listen_state(self.tick, "sensor.solar_radiation", duration=90)
        self.listen_state(self.tick, "sensor.outdoor_temperature", duration=150)
        self.listen_state(self.tick, "sensor.indoor_temperature", duration=120)

        self.log(
            f"[Init] {self.args.get('entity_id','unknown')} ready: "
            f"{self.NUM_BLINDS} blinds total | "
            f"tick={self.TICK_INTERVAL}s | offset={offset:.1f}s"
        )


    # ----------------------
    # Event callbacks
    # ----------------------
    def window_closed(self, entity, attribute, old, new, kwargs):
        if "contact" in self.args:
            self.log(
                f"Window {self.args['contact']} closed. "
                f"Kill switch for {DEFAULT_EVENT_KILL_SWITCH_DURATION} min activated."
            )
            self.b.SetWindowClosed()

    def wind_alarm_off(self, entity, attribute, old, new, kwargs):
        self.log("Wind alarm is off, releasing kill switch.")
        self.release_kill_switch(None)

    def light_on(self, entity, attribute, old, new, kwargs):
        # Keep Previous behavior: raise dark threshold when interior light is on
        self.log(f"Raising lux dark threshold to {self.sun_app.get_Lux_dark_with_light_inside()} (light ON).", level="DEBUG")
        try:
            self.b.SetLuxDark(self.sun_app.get_Lux_dark_with_light_inside())
        except Exception:
            pass

    def light_off(self, entity, attribute, old, new, kwargs):
        # Only reset when ALL dawn_lights are off (kept from Previous)
        for light in self.args.get("dawn_lights", []):
            if self.get_state(light) == "on":
                return

        self.log(f"Resetting lux dark threshold to {self.sun_app.get_lux_dark()} (all lights OFF).", level="DEBUG")
        try:
            self.b.SetLuxDark(self.sun_app.get_lux_dark())
        except Exception:
            pass

        # Evening safeguard from Previous: if after 15:00, blinds down (0%), hold them with kill switch
        if datetime.datetime.now().hour > EVENING_HOUR_THRESHOLD:
            pos = self.get_state(self.args["blind"], attribute="current_position")
            if pos is not None:
                try:
                    if int(pos) == 0:
                        self.b.SetKillSwitch(DEFAULT_EVENT_KILL_SWITCH_DURATION)
                except Exception:
                    pass

    # ----------------------
    # Helpers
    # ----------------------
    def is_not_a_number(self, value):
      return value == 'unknown' or value == 'unavailable' or value is None

    def evaluate_runtime(self):
        """Read optional per-blind runtime; default to tilt delay."""
        default_runtime = self.DEFAULT_TILT_DELAY
        if "blind_runtime" not in self.args:
            return default_runtime

        try:
            return float(self.get_state(self.args["blind_runtime"]))
        except (TypeError, ValueError):
            self.log("Invalid blind_runtime value, using default.")
            return default_runtime

    def _get_inside_temperature(self):
        """Best-effort inside temp read with/without thermostat attribute."""
        # If user set a flag saying the entity isn't a climate/thermostat
        if self.args.get('inside_temperature_is_no_thermostat'):
            t = self.get_state(self.args["inside_temperature"])
            if t is not None:
                return t

        # Try climate attribute first (classic setup)
        t = self.get_state(self.args.get('inside_temperature'), attribute='current_temperature')
        if t is not None:
            return t

        # Fallback: plain sensor value
        t = self.get_state(self.args.get("inside_temperature"))
        if t is not None:
            return t

        self.log("Cannot read inside temperature", level="ERROR")
        return "unknown"

    def get_valid_knx_position(self):
        """
        Safely retrieves the current KNX position for the blind.
        Handles unknown or missing states gracefully without blocking AppDaemon.
        Returns:
            int: Current position (0–100)
            None: If no valid position available yet
        """
        pos = self.get_state(self.args["blind"], attribute="current_position")

        if pos in [None, "unknown"]:
            if not hasattr(self, "knx_first_unknown_ts"):
                self.knx_first_unknown_ts = self.datetime()
                self.knx_last_valid_pos = None
                self.log("KNX position unknown (startup or bus delay). Will retry in 30s.", level="WARNING")
                self.run_in(self.retry_knx_sync, 30)
                return None

            delay = (self.datetime() - self.knx_first_unknown_ts).total_seconds()

            if delay < 300:  # tolerate 5 minutes
                self.log(f"KNX position still unknown after {int(delay)}s, retrying later.", level="WARNING")
                self.run_in(self.retry_knx_sync, 30)
                return None

            # Fallback after 5 minutes
            self.log("KNX feedback missing >5 min, using last known position or fallback=50.", level="WARNING")
            return getattr(self, "knx_last_valid_pos", None) or 50

        try:
            self.knx_last_valid_pos = int(pos)
            if hasattr(self, "knx_first_unknown_ts"):
                del self.knx_first_unknown_ts
            return self.knx_last_valid_pos
        except Exception as e:
            self.log(f"Error parsing KNX position ({pos}): {e}", level="ERROR")
            return getattr(self, "knx_last_valid_pos", None) or 50

    def retry_knx_sync(self, kwargs):
        pos = self.get_state(self.args["blind"], attribute="current_position")
        if pos not in [None, "unknown"]:
            try:
                self.knx_last_valid_pos = int(pos)
            except Exception:
                self.knx_last_valid_pos = 50
            self.log(f"KNX position restored: {pos}", level="INFO")
            # run a full evaluation
            self.evaluate()
        else:
            self.log("KNX still unknown after retry. Keeping current state.", level="WARNING")

    def set_state_reason(self, reason):
        """Push the decision reason to a status helper entity (UI feedback)."""
        try:
            entity = f"input_text.{self.args['blind'].replace('cover.', '')}_status"
            self.call_service("input_text/set_value", entity_id=entity, value=reason)
        except Exception:
            # don't crash on missing helper
            pass

    def release_kill_switch(self, _unused):
        self.log("KillSwitch released")
        self.b.ReleaseKillSwitch()

    # ----------------------
    # Periodic tick & evaluation
    # ----------------------
    def tick(self, _unused):
        # prevent concurrency while a movement scenario is running
        if self.b.GetMasterLock():
            self.log(f"Masterlock active for {self.args['blind']}")
            return

        # Global kill-switch (Previous behavior)
        try:
            global_kill_switch = self.get_state("switch.raffstore_kill_switch")
            if global_kill_switch == 'on':
                self.set_state_reason("Global Kill switch is on. All blinds are controlled manually.")
                return
        except Exception:
            pass

        # Outside temperature via max_temp_app (New behavior)
        outside_temp = self.max_temp_app.get_outside_temperature()
        if self.is_not_a_number(outside_temp):
            self.set_state_reason('Unknown outside temperature. Doing nothing.')
            return
        self.b.SetOutsideTemperature(float(outside_temp))


        # Inside temperature (merged robust read)
        inside_temp = self._get_inside_temperature()
        if self.is_not_a_number(inside_temp):
            self.set_state_reason("Unknown inside temperature.")
            return
        self.b.SetInsideTemperature(float(inside_temp))

        # Lux + sun geometry (New behavior)
        try:
            self.b.SetLux(self.sun_app.get_lux_last_10_minutes())
        except Exception:
            # fallback to previous helper entity
            try:
                self.b.SetLux(float(self.get_state("input_number.sun_lux_10_minute_average")))
            except Exception:
                pass

        try:
            self.b.SetAzimuth(float(self.get_state("sun.sun", attribute="azimuth")))
            self.b.SetElevation(float(self.get_state("sun.sun", attribute="elevation")))
        except Exception:
            pass

        # Contact state every tick (Previous)
        if "contact" in self.args:
            try:
                self.b.SetReedContact(self.get_state(self.args["contact"]) == "on")
            except Exception:
                pass

        # Wind lock every tick (support both ways)
        wind_lock = False
        try:
            if "wind_alarm" in self.args:
                wind_lock = (self.get_state(self.args["wind_alarm"]) == "on")
            else:
                wind_lock = (self.get_state(self.app_config["wind"]["wind_alarm"]) == "on")
        except Exception:
            pass
        self.b.SetWindLock(wind_lock)

        # Do the full evaluation & act (Previous behavior)
        self.evaluate()

    def evaluate(self):
        """Check if we need to do something with the blinds, then act."""
        pos = self.get_valid_knx_position()
        if pos is None:
            self.set_state_reason("Unknown real-life KNX position. Waiting for KNX feedback.")
            return

        # Tilt position: either same entity as blind (tilt attr) or a dedicated 'blind_tilt_position'
        if 'blind_tilt_position' not in self.args:
            tilt = self.get_state(self.args["blind"], attribute="current_tilt_position")
        else:
            tilt = self.get_state(self.args["blind_tilt_position"], attribute="current_position")

        # Optional 10% precision mode
        if self.args.get("use_10_percent_precision"):
            pos = int(pos / 10) * 10
            if tilt is not None:
                try:
                    tilt = int(int(tilt) / 10) * 10
                except Exception:
                    pass

        self.knx_current_angle = tilt
        self.b.SetKNXPositions(pos, tilt)
        action_needed = self.b.Evaluate()

        for log_line in self.b.FlushLog():
            self.log(f"[Evaluate] {log_line}")


        self.set_state_reason(self.b.GetDesiredPositionReason())

        if action_needed:
            self.b.SetMasterLock()
            self._move_blind(pos, tilt)

    def _move_blind(self, current_pos, current_angle):
        position = self.b.GetDesiredPosition()
        tilt_position = self.b.GetDesiredAngle()

        if position != current_pos:
            self.log(f"Setting position to {position} for {self.args['blind']}")
            self.call_service("cover/set_cover_position", entity_id=self.args["blind"], position=position)

        if tilt_position is None:
            self.log("Blinds do not support tilt. Skipping tilt.")
            self.b.UnsetMasterLock()
            return

        # If we know the cover runtime and they go down, stop then set tilt earlier
        if "blind_runtime" in self.args and position == self.b.DOWN:
            self.run_in(
                self.set_tilt,
                self.evaluate_runtime(),
                tilt_position=tilt_position,
                position=position,
                knx_current_angle=current_angle,
                stop=True
            )
        else:
            self.run_in(
                self.set_tilt,
                self.DEFAULT_TILT_DELAY,
                tilt_position=tilt_position,
                position=position,
                knx_current_angle=current_angle
            )

    def set_tilt(self, kwargs):
        # Stop before tilting if requested (faster + more precise)
        if kwargs.get('stop'):
            self.log("Stopping blind for tilt adjustment.")
            self.call_service("cover/stop_cover", entity_id=self.args["blind"])
            time.sleep(1.0)  # allow KNX/HA to update current position

        tilt_position = kwargs.get('tilt_position')
        self.log(f"Changing tilt for {self.args['blind']} from {self.knx_current_angle} to {tilt_position}")

        if 'blind_tilt_position' not in self.args:
            self.call_service(
                "cover/set_cover_tilt_position",
                entity_id=self.args["blind"],
                tilt_position=tilt_position
            )
        else:
            self.call_service(
                "cover/set_cover_position",
                entity_id=self.args["blind_tilt_position"],
                position=tilt_position
            )

        self.b.UnsetMasterLock()

    # ----------------------
    # Max/Min outside temperature
    # ----------------------
    def set_max_outside_temp(self, _unused):
        """
        Prefer data from max_temp app (New); fallback to Previous entities.
        """
        try:
            max_val, min_val = self.max_temp_app.get_yesterday_extremes()
            msg = self.b.SetMaxOutsideTemperature(max_val, min_val)
            self.log(msg)
            return
        except Exception as e:
            self.log(f"max_temp_app.get_yesterday_extremes failed: {e}. Falling back to entities.", level="WARNING")

        # Fallback to Previous behavior with entities
        try:
            max_temp = float(self.get_state(self.app_config["max_temp"]["max_temp_sensor_yesterday"]))
        except Exception:
            max_temp = 24
            self.log(f"Defaulting max temp to {max_temp}", level="WARNING")

        try:
            min_temp = float(self.get_state(self.args["min_temp_sensor_value_yesterday"]))
        except Exception:
            min_temp = 21
            self.log(f"Defaulting min temp to {min_temp}", level="WARNING")

        msg = self.b.SetMaxOutsideTemperature(max_temp, min_temp)
        self.log(msg)
