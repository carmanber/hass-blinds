# -*- coding: utf-8 -*-
import appdaemon.plugins.hass.hassapi as hass
import datetime

class MaxTemp(hass.Hass):

  def initialize(self):
    self.log("Initializing Maximum Temperature system...")
    self.listen_state(self.temperature_update, entity_id=self.args["outside_temp_sensor"])
    time = datetime.time(0, 0, 0)
    self.run_daily(self.reset, time)

  def safe_float(self, val):
      try:
          return float(val)
      except (TypeError, ValueError):
          return None

  def temperature_update(self, entity, attribute, old, new, kwargs):
      new_temp = self.safe_float(new)
      if new_temp is None:
          return

      max_temp = self.safe_float(self.get_state(self.args["max_temp_sensor"]))
      min_temp = self.safe_float(self.get_state(self.args["min_temp_sensor"]))

      if max_temp is None or min_temp is None:
          self.log("Stored max/min temperature is unavailable.", level="WARNING")
          return

      if new_temp > max_temp:
          self.call_service("input_number/set_value", entity_id=self.args["max_temp_sensor"], value=new_temp)
          self.log(f"New daily max temperature: {new_temp:.1f}°C")

      if new_temp < min_temp:
          self.call_service("input_number/set_value", entity_id=self.args["min_temp_sensor"], value=new_temp)
          self.log(f"New daily min temperature: {new_temp:.1f}°C")


  def reset(self, _unused):
    # reset this and store the old value somewhere
    self.call_service("input_number/set_value",
        entity_id=self.args["max_temp_sensor_yesterday"],
        value=self.get_state(self.args["max_temp_sensor"]))

    self.call_service("input_number/set_value",
        entity_id=self.args["max_temp_sensor"],
        value=self.get_state(self.args["outside_temp_sensor"]))
    
    self.call_service("input_number/set_value",
        entity_id=self.args["min_temp_sensor_yesterday"],
        value=self.get_state(self.args["min_temp_sensor"]))

    self.call_service("input_number/set_value",
        entity_id=self.args["min_temp_sensor"],
        value=self.get_state(self.args["outside_temp_sensor"]))