# -*- coding: utf-8 -*-
import appdaemon.plugins.hass.hassapi as hass
import datetime
from lib import blinds_lib
import time
from lib.sun_lib import Sun
from lib.blinds_lib import EVENING_HOUR_THRESHOLD
from lib.blinds_lib import DEFAULT_EVENT_KILL_SWITCH_DURATION

class Blinds(hass.Hass, Sun):
  DEFAULT_TILT_DELAY = 85

  def initialize(self):
    self.log("Initializing blinds...")
    self.b = blinds_lib.Blind(**self.args["blind_config"])
    self.tick(None)

    next_tick = datetime.datetime.now() + datetime.timedelta(seconds=60)
    self.run_every(self.tick, next_tick, 60)
    self.run_daily(self.set_max_outside_temp, datetime.time(1, 1, 3))

    if "min_temp_sensor_value_yesterday" not in self.args:
      self.args["min_temp_sensor_value_yesterday"] = (
        "input_number.yesterdays_min_outside_temp_over_24_hours"
      )
    
    self.set_max_outside_temp(None)    

    if "contact" in self.args:
      self.b.SetDoorType()
      self.listen_state(
        self.window_closed, 
        entity_id=self.args["contact"], 
        new="off", 
        old="on")

    if "wind_alarm" in self.args:
      self.listen_state(
        self.wind_alarm_off, 
        entity_id=self.args["wind_alarm"], 
        new="off", 
        old="on")

    if "dawn_lights" in self.args:
      for light in self.args["dawn_lights"]:
        self.listen_state(self.light_on, entity_id=light, new="on", old="off")
        self.listen_state(self.light_off, entity_id=light, new="off", old="on")

  def window_closed(self, entity, attribute, old, new, kwargs):
    if "contact" in self.args:
      self.log(f"Window {self.args['contact']} closed. Kill switch for {DEFAULT_EVENT_KILL_SWITCH_DURATION} min activated.")
      self.b.SetWindowClosed()

  def wind_alarm_off(self, entity, attribute, old, new, kwargs):
    self.log("Wind alarm is off, releasing kill switch.")
    self.release_kill_switch(None)

  def light_on(self, entity, attribute, old, new, kwargs):
    self.log("raising lux threshold to %s" % Sun.LUX_DARK_WITH_LIGHT_INSIDE)
    self.b.SetLuxDark(Sun.LUX_DARK_WITH_LIGHT_INSIDE)

  def light_off(self, entity, attribute, old, new, kwargs):
    # We need to check if all lights are off, only then we can reset the lux for darkness.
    for light in self.args["dawn_lights"]:
      if self.get_state(light) == "on":
        return
    # If all lights are off, reset the darkness threshold to DARK.
    self.log(f"Resetting lux threshold to {Sun.LUX_DARK}")
    self.b.SetLuxDark(Sun.LUX_DARK)

    # if someone turns the last light off and this is after 3pm AND the blinds are down then don't let them go up anymore for 30 minutes. the reason for this is that by setting the LuxDark threshold to a lower value it could go up again, and that is totally unnecessary.
    if datetime.datetime.now().hour > EVENING_HOUR_THRESHOLD:
      pos = self.get_state(self.args["blind"], attribute="current_position") 
      if pos is not None and int(pos) == 0:
        self.b.SetKillSwitch(DEFAULT_EVENT_KILL_SWITCH_DURATION)

  def evaluate_runtime(self):
    default_runtime = self.DEFAULT_TILT_DELAY
    if "blind_runtime" not in self.args:
      return default_runtime

    try:
        return float(self.get_state(self.args["blind_runtime"]))
    except (TypeError, ValueError):
        self.log("Invalid blind_runtime value, using default.")
        return default_runtime
      
      
  def evaluate(self):
    """Check if we need to do something with the blinds. """
    pos = self.get_state(self.args["blind"], attribute="current_position")
    if pos is None:
      self.set_state_reason("Unknown real-life knx position. Doing nothing.")
      return

    if 'blind_tilt_position' not in self.args:
      tilt = self.get_state(self.args["blind"], attribute="current_tilt_position")
    else:
      tilt = self.get_state(self.args["blind_tilt_position"], attribute="current_position")

    # because blind positions can vary when changing the tilt (!) we round on 10 percent precision.
    if self.args.get("use_10_percent_precision"):
      pos = int(pos / 10) * 10
      if tilt is not None:
        tilt = int(tilt / 10) * 10

    self.knx_current_angle = tilt
    self.b.SetKNXPositions(pos, tilt)
    action_needed = self.b.Evaluate()

    for log_line in self.b.FlushLog():
      self.log(log_line)

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
        # if the position is UP, then don't set the tilt anymore. -> results in: Turning Kill switch on due to angle mismatch: last_postion: 100, knx_current_position: 90, last_angle: 100, knx_current_angle: 0
      
      if tilt_position is None:
        self.log("Blinds do not support tilt. Skipping.")
        self.b.UnsetMasterLock()
        return
      
      # if we know how long the cover runs and they go down, then set the parameters so that the blinds can stop and set the angle faster.
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
    # if we were told to stop then stop the cover.
    if kwargs.get('stop'):
      self.log("Stopping blind for tilt adjustment.")
      self.call_service("cover/stop_cover", entity_id=self.args["blind"])
      # wait half a second for the system to report back the current position.
      time.sleep(1.0)
    # there is no need to change the tilt position when we send the blinds up.
    # However, we have to unset the MasterLock

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

  def release_kill_switch(self, _unused):
    self.log("releasing kill switch because why not")
    self.b.ReleaseKillSwitch()

  def set_state_reason(self, reason):
    self.log(reason)
    entity = f"input_text.{self.args['blind'].replace('cover.', '')}_status"
    self.call_service("input_text/set_value", entity_id=entity, value=reason) 

  def set_max_outside_temp(self, _unused):
    try:
      max_temp = float(self.get_state(self.app_config["max_temp"]["max_temp_sensor_yesterday"]))
    except Exception:
      max_temp = 24
      self.log(f"Defaulting max temp to {max_temp}")

    try:
      min_temp = float(self.get_state(self.args["min_temp_sensor_value_yesterday"]))
    except Exception:
      min_temp = 21
      self.log(f"Defaulting min temp to {min_temp}")

    msg = self.b.SetMaxOutsideTemperature(max_temp, min_temp)
    self.log(msg)

  def tick(self, _unused_): # , entity, attribute, old, new, kwargs):
    # this prevents concurrency issues where blinds run longer than 60 seconds.
    if self.b.GetMasterLock():
      self.log(f"Masterlock active for {self.args['blind']}")
      return
    
    global_kill_switch = self.get_state("switch.raffstore_kill_switch")
    if global_kill_switch == 'on':
      self.set_state_reason("Global Kill switch is on. All blinds are controlled manually.")
      return
    
    outside_temp = self.get_state(self.app_config["max_temp"]["outside_temp_sensor"])
    if self.is_not_a_number(outside_temp):
      self.set_state_reason('Unknown outside temperature. Doing nothing.')
      return
    self.b.SetOutsideTemperature(float(outside_temp))

    # Get inside temperature
    inside_temp = self._get_inside_temperature()
    if self.is_not_a_number(inside_temp):
      self.set_state_reason("Unknown inside temperature.")
      return
    self.b.SetInsideTemperature(float(inside_temp))
        
    self.b.SetLux(float(self.get_state("input_number.sun_lux_10_minute_average")))
    self.b.SetAzimuth(float(self.get_state("sun.sun", attribute="azimuth")))
    self.b.SetElevation(float(self.get_state("sun.sun", attribute="elevation")))

    if "contact" in self.args:
      self.b.SetReedContact(self.get_state(self.args["contact"]) == "on")

    wind_lock = self.get_state(self.app_config["wind"]["wind_alarm"]) == "on"
    self.b.SetWindLock(wind_lock)

    self.evaluate()

  def _get_inside_temperature(self):
    temp = self.get_state(self.args['inside_temperature'], attribute='current_temperature')
    if temp is not None:
      return temp
    temp = self.get_state(self.args["inside_temperature"])
    if temp is not None:
      return temp

    self.log("Cannot read inside temperature", level="ERROR")
    return "unknown"